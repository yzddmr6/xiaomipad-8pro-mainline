// SPDX-License-Identifier: MIT
// Synthetic protocol/lifecycle tests are not evidence of physical finger tracking.
#include "calibration.hpp"
#include "live_pipeline.hpp"
#include "orientation.hpp"
#include "panel.hpp"
#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>
#include <stdexcept>

namespace {
void check(bool result, const char *message) {
    if (!result)
        throw std::runtime_error(message);
}

template<typename Function> void rejects(Function call, const char *message) {
    try { call(); } catch (const std::runtime_error &) { return; }
    throw std::runtime_error(message);
}

void put(std::span<uint8_t> data, size_t offset, uint64_t value, size_t length) {
    for (size_t index = 0; index < length; ++index)
        data[offset + index] = value >> (index * 8);
}

uint64_t get(std::span<const uint8_t> data, size_t offset, size_t length) {
    uint64_t value = 0;
    for (size_t index = 0; index < length; ++index)
        value |= uint64_t{data[offset + index]} << (index * 8);
    return value;
}

std::vector<uint8_t> wire(std::span<const uint8_t> payload, uint64_t sequence,
                          uint64_t timestamp, uint16_t flags) {
    std::vector<uint8_t> data(32 + 257 + payload.size());
    put(data, 0, 0x3150544e, 4);
    put(data, 4, 32, 2);
    put(data, 6, 257 + payload.size(), 2);
    put(data, 8, sequence, 8);
    put(data, 16, timestamp, 8);
    put(data, 24, get(payload, 4, 2), 2);
    put(data, 26, flags, 2);
    put(data, 28, get(payload, 8, 4), 4);
    std::copy(payload.begin(), payload.end(), data.begin() + 32 + 257);
    return data;
}

void checksum(std::span<uint8_t> payload) {
    uint32_t sum = 0;
    for (size_t offset = 20; offset < 20 + get(payload, 8, 4) * 4; offset += 2)
        sum += get(payload, offset, 2);
    const uint16_t crc = uint16_t(-sum);
    put(payload, 4, crc, 2);
    put(payload, 12, uint16_t(~crc), 2);
}

std::vector<uint8_t> packet(uint16_t counter, bool touch = false, uint8_t type = 3,
                            uint8_t scan = 4) {
    std::vector<uint8_t> payload(5192);
    payload[0] = 0xa5;
    put(payload, 2, counter, 2);
    put(payload, 8, 0x4c3, 4);
    put(payload, 16, uint32_t(~0x4c3), 4);
    payload[24] = scan;
    payload[48] = nvt::kColumns;
    payload[49] = nvt::kRows;
    payload[56] = type;
    for (int row = 0; row < nvt::kRows; ++row) {
        for (int column = 0; column < nvt::kColumns; ++column) {
            int value = 10000;
            const int x = column - 30, y = row - 20;
            if (touch && std::abs(x) <= 1 && std::abs(y) <= 1)
                value += !x && !y ? 1000 : (!x || !y ? 500 : 250);
            put(payload, 64 + 2 * (row * nvt::kColumns + column), value, 2);
        }
    }
    checksum(payload);
    return payload;
}

nvt::Calibration calibration() {
    nvt::Calibration value{};
    for (int row = 0; row < nvt::kRows; ++row)
        value.row_map[row] = (row + 1) * 10;
    for (int column = 0; column < nvt::kColumns; ++column)
        value.column_map[column] = (column + 1) * 10;
    value.row_maximum = 4100;
    value.column_maximum = 6100;
    value.edge_size = 100;
    value.edge_base = value.edge_gain = 1000;
    return value;
}

struct Fixture {
    std::vector<nvt::Slot> visible;
    size_t reports = 0;
    size_t pen_frames = 0;
    uint64_t pen_timestamp = 0;
    nvt::LivePipeline pipeline{calibration(), [&](auto slots) {
        visible.assign(slots.begin(), slots.end());
        ++reports;
    }, [&](const nvt::PenFrame &, uint64_t timestamp_ns) {
        ++pen_frames;
        pen_timestamp = timestamp_ns;
    }};
    nvt::StreamReader reader;
    uint64_t sequence = 0, timestamp = 1000000000;

    void feed(std::span<const uint8_t> payload, uint16_t flags = nvt::kStreamValid,
              size_t chunk = 127) {
        ++sequence;
        timestamp += 8333333;
        const auto bytes = wire(payload, sequence, timestamp, flags);
        for (size_t offset = 0; offset < bytes.size(); offset += chunk)
            reader.feed(std::span(bytes).subspan(offset, std::min(chunk, bytes.size() - offset)),
                        [&](const auto &record) { pipeline.feed(record, record.timestamp_ns); });
    }

    void activate() {
        feed(packet(0, false, 3, 2), nvt::kStreamValid | nvt::kStreamEpoch);
        for (uint16_t index = 1; index <= 4; ++index)
            feed(packet(index, true));
        check(visible.size() == 1, "synthetic pipeline did not activate a slot");
        check(visible[0].contact.x == 3100 && visible[0].contact.y == 2100,
              "synthetic processor axes were transformed unexpectedly");
    }
};

void framing() {
    const auto payload = packet(42);
    const auto bytes = wire(payload, 19, 1000000000, nvt::kStreamValid);
    for (const size_t chunk : {size_t{1}, size_t{31}, size_t{32}, size_t{257}, bytes.size()}) {
        nvt::StreamReader reader;
        unsigned int calls = 0;
        auto consume = [&](const nvt::StreamRecord &record) {
            ++calls;
            check(record.sequence == 19 && record.timestamp_ns == 1000000000,
                  "NTP1 endian/sequence mismatch");
            check(std::equal(record.payload.begin(), record.payload.end(), payload.begin(), payload.end()),
                  "NTP1 payload differs");
        };
        for (unsigned int repeat = 0; repeat < 2; ++repeat)
            for (size_t offset = 0; offset < bytes.size(); offset += chunk)
                reader.feed(std::span(bytes).subspan(offset, std::min(chunk, bytes.size() - offset)), consume);
        reader.finish();
        check(calls == 2, "split/concatenated record count mismatch");
    }
    for (const size_t length : {size_t{1}, size_t{31}, size_t{32}, bytes.size() - 1}) {
        nvt::StreamReader reader;
        reader.feed(std::span(bytes).first(length), [](auto &) {});
        rejects([&] { reader.finish(); }, "truncated stream accepted");
    }
    for (const auto &[offset, value] : std::array<std::pair<size_t, uint8_t>, 5>{
            {{0, 0}, {4, 31}, {6, 0}, {7, 0xff}, {26, 0x80}}}) {
        auto bad = bytes;
        bad[offset] = value;
        if (offset == 6)
            bad[7] = 0;
        nvt::StreamReader reader;
        rejects([&] { reader.feed(bad, [](auto &) {}); }, "bad stream header accepted");
    }
}

void midStreamEpoch() {
    auto twoTouches = [](uint16_t counter) {
        auto payload = packet(counter, true);
        for (int y = -1; y <= 1; ++y)
            for (int x = -1; x <= 1; ++x)
                put(payload, 64 + 2 * ((30 + y) * nvt::kColumns + 45 + x),
                    10000 + (!x && !y ? 1000 : (!x || !y ? 500 : 250)), 2);
        checksum(payload);
        return payload;
    };
    // A restart can first deliver either finger data or a startup packet.
    for (const uint8_t type : {3, 14}) {
        Fixture f;
        f.feed(packet(0, false, 3, 2), nvt::kStreamValid | nvt::kStreamEpoch);
        for (uint16_t counter = 1; counter <= 6; ++counter)
            f.feed(twoTouches(counter));
        check(f.visible.size() == 2, "mid-stream EPOCH setup needs two contacts");
        const auto reports = f.reports;
        const auto processed = f.pipeline.stats.processed;
        const auto baselines = f.pipeline.stats.baselines;
        // Sequence, time and firmware counter are deliberately contiguous:
        // only EPOCH can distinguish this first frame from the previous run.
        auto first = twoTouches(7);
        first[56] = type;
        checksum(first);
        f.feed(first, nvt::kStreamValid | nvt::kStreamEpoch, 1);
        check(f.visible.empty() && f.reports == reports + 1,
              "mid-stream EPOCH did not release all contacts exactly once");
        check(f.pipeline.stats.processed == processed &&
              f.pipeline.stats.baselines == baselines,
              "mid-stream EPOCH first frame reused the old baseline");
        for (uint16_t counter = 8; counter <= 17; ++counter)
            f.feed(twoTouches(counter));
        check(f.visible.empty() && f.pipeline.stats.processed == processed &&
              f.pipeline.stats.baselines == baselines,
              "mid-stream EPOCH did not wait for a fresh baseline");
        f.feed(packet(18, false, 3, 2));
        check(f.pipeline.stats.baselines == baselines + 1 && f.visible.empty(),
              "mid-stream EPOCH baseline did not restart processing");
        for (uint16_t counter = 19; counter <= 24; ++counter)
            f.feed(twoTouches(counter));
        check(f.visible.size() == 2 && !f.pipeline.stats.invalid &&
              f.pipeline.stats.discontinuities == 2,
              "mid-stream EPOCH did not resume with fresh contacts");
        f.reader.finish();
    }
    std::cout << "PASS: mid-stream EPOCH releases two contacts, waits for baseline and resumes\n";
}

void lifecycle() {
    Fixture f;
    f.activate();
    f.pipeline.expire(f.timestamp + nvt::LivePipeline::kStallNs - 1);
    check(!f.visible.empty(), "released before stall deadline");
    f.pipeline.expire(f.timestamp + nvt::LivePipeline::kStallNs);
    check(f.visible.empty() && f.pipeline.stats.stalls == 1, "stall did not release touch");
    f.activate();
    f.feed(packet(5, false, 29));
    check(f.pipeline.stats.pen_frames == 1 && f.pen_frames == 1, "pen frame did not reach the pen path");
    check(f.pipeline.stats.other_types == 0, "pen frame was counted as another type");
    check(f.visible.empty(), "a valid pen frame did not release the fingers");
    check(f.pen_timestamp == f.timestamp, "the pen frame lost the timestamp of the stream");
    f.timestamp += nvt::LivePipeline::kStallNs;
    f.feed(packet(6, false, 29));
    check(f.visible.empty(), "pen traffic kept stale finger alive");
    check(f.pen_frames == 2, "the second pen frame did not reach the pen path");
    f.activate();
    f.feed(packet(7), 0);
    check(f.visible.empty() && f.pipeline.stats.invalid == 1, "invalid flag did not release touch");
    f.activate();
    auto bad = packet(8);
    bad[64] ^= 1;
    f.feed(bad);
    check(f.visible.empty() && f.pipeline.stats.invalid == 2, "bad checksum did not release touch");
    f.activate();
    ++f.sequence;
    f.feed(packet(9, true));
    check(f.visible.empty(), "sequence gap did not release touch");
    f.activate();
    --f.sequence;
    f.feed(packet(10, true));
    check(f.visible.empty() && f.pipeline.stats.invalid == 3, "duplicate sequence accepted");
    f.activate();
    f.timestamp -= 10000000;
    f.feed(packet(11, true));
    check(f.visible.empty() && f.pipeline.stats.invalid == 4, "reversed time accepted");
    f.activate();
    f.feed(packet(12, false, 3, 2), nvt::kStreamValid | nvt::kStreamEpoch);
    check(f.visible.empty(), "new firmware epoch retained a contact");
    f.activate();
    const auto stale = wire(packet(13, true), ++f.sequence, f.timestamp + 8333333, nvt::kStreamValid);
    f.reader.feed(stale, [&](const auto &record) {
        f.pipeline.feed(record, record.timestamp_ns + nvt::LivePipeline::kStallNs);
    });
    check(f.visible.empty() && f.pipeline.stats.stale == 1, "queued stale contact was replayed");
    f.timestamp += 8333333;
    f.activate();
    const auto future = wire(packet(14, true), ++f.sequence, f.timestamp + 8333333, nvt::kStreamValid);
    f.reader.feed(future, [&](const auto &record) {
        f.pipeline.feed(record, record.timestamp_ns - 1);
    });
    check(f.visible.empty() && f.pipeline.stats.stale == 2, "future timestamp was accepted");
    f.reader.finish();
}

void orientation() {
    auto value = calibration();
    value.column_maximum = 32000;
    value.row_maximum = 21360;
    const auto axes = nvt::panelAxes(value);
    check(axes.width == 32000 && axes.height == 21360, "panel ranges do not follow the panel axes");
    // Processor coordinates of recorded corner taps, in recorded order: top-left,
    // bottom-left, bottom-right and top-right.
    constexpr std::array<std::array<int, 4>, 5> corners{{
        {520, 21291, 520, 68}, {1603, 661, 1603, 20698}, {31412, 384, 31412, 20975},
        {31198, 20866, 31198, 493}, {-5, 40000, 0, 0}}};
    std::vector<nvt::Slot> slots(corners.size()), panel;
    for (size_t index = 0; index < corners.size(); ++index) {
        slots[index].number = int(index);
        slots[index].contact.x = corners[index][0];
        slots[index].contact.y = corners[index][1];
    }
    nvt::panelSlots(slots, value, nvt::PanelVendor::Csot, panel);
    for (size_t index = 0; index < corners.size(); ++index)
        check(panel[index].number == int(index) && panel[index].contact.x == corners[index][2] &&
              panel[index].contact.y == corners[index][3], "recorded corner mapped to the wrong panel position");
    const auto &tl = panel[0].contact, &bl = panel[1].contact, &br = panel[2].contact, &tr = panel[3].contact;
    check(tl.x < tr.x && bl.x < br.x && tl.y < bl.y && tr.y < br.y, "panel corners are not upright");

    const auto at = [&](int x, int y) {
        nvt::Contact contact;
        contact.x = x;
        contact.y = y;
        return nvt::panelContact(contact, value, nvt::PanelVendor::Csot);
    };
    // Left-to-right swipe: constant processor row, increasing processor column.
    nvt::Contact previous;
    for (int column : {141, 9000, 18000, 27795}) {
        const auto point = at(column, 12000);
        check(point.y == 21359 - 12000 && (column == 141 || point.x > previous.x),
              "left-to-right swipe does not increase X at constant Y");
        previous = point;
    }
    // Top-to-bottom swipe: constant processor column, processor row falling from the top edge.
    for (int row : {21108, 14000, 7000, 37}) {
        const auto point = at(16000, row);
        check(point.x == 16000 && (row == 21108 || point.y > previous.y),
              "top-to-bottom swipe does not increase Y at constant X");
        previous = point;
    }
    // Recorded swipe endpoints as emitted by an earlier transform (X, Y) become (Y, X) now.
    constexpr std::array<std::array<int, 2>, 4> emitted{{{8813, 141}, {9442, 27795}, {251, 16057}, {21322, 15722}}};
    for (const auto &old : emitted) {
        const auto point = at(old[1], 21359 - old[0]);
        check(point.x == old[1] && point.y == old[0], "recorded swipe endpoint is not the transposed output");
    }
    // BOE: processor Y already runs from the top edge to the bottom edge; X is unchanged.
    const auto boe = [&](int x, int y) {
        nvt::Contact contact;
        contact.x = x;
        contact.y = y;
        return nvt::panelContact(contact, value, nvt::PanelVendor::Boe);
    };
    for (int row : {37, 7000, 14000, 21108}) {
        const auto point = boe(16000, row);
        check(point.x == 16000 && point.y == row, "BOE top-to-bottom swipe is not processor Y");
    }
    for (int column : {141, 9000, 27795}) {
        const auto point = boe(column, 12000);
        check(point.x == column && point.y == 12000, "BOE left-to-right swipe is not processor X");
    }
    const auto clamped = boe(-5, 40000);
    check(clamped.x == 0 && clamped.y == 21359, "BOE contact outside the panel was not clamped");
    std::vector<nvt::Slot> boe_panel;
    nvt::panelSlots(slots, value, nvt::PanelVendor::Boe, boe_panel);
    check(boe_panel[0].contact.x == 520 && boe_panel[0].contact.y == 21291, "BOE slots are flipped");
}

struct TemporaryTree {
    std::filesystem::path root;
    TemporaryTree() {
        std::string pattern = (std::filesystem::temp_directory_path() / "piano-thp-panel-XXXXXX").string();
        check(mkdtemp(pattern.data()) != nullptr, "cannot create temporary sysfs tree");
        root = pattern;
    }
    ~TemporaryTree() { std::filesystem::remove_all(root); }
    void write(const std::filesystem::path &relative, const std::string &text) const {
        std::filesystem::create_directories((root / relative).parent_path());
        std::ofstream(root / relative) << text;
    }
};

void panel() {
    TemporaryTree tree;
    const auto sysfs = tree.root / "sys";
    // The service opens the udev link; the kernel names the misc node nvt-thp-<SPI device>.
    tree.write("dev/nvt-thp-spi0.0", "");
    std::filesystem::create_symlink("nvt-thp-spi0.0", tree.root / "dev/piano-touch");
    const auto node = tree.root / "dev/piano-touch";
    const auto attribute = std::filesystem::path("sys/class/misc/nvt-thp-spi0.0/device/panel_vendor");

    auto identity = nvt::readPanelVendor(node, sysfs);
    check(identity.vendor == nvt::PanelVendor::Csot && !identity.reported,
          "a kernel without panel_vendor must mean CSOT");
    check(nvt::describePanel(identity) == "THP panel: csot (kernel has no panel_vendor; assuming csot)",
          "missing panel_vendor log line");
    tree.write(attribute, "csot\n");
    identity = nvt::readPanelVendor(node, sysfs);
    check(identity.vendor == nvt::PanelVendor::Csot && identity.reported, "kernel CSOT not read");
    tree.write(attribute, "boe\n");
    identity = nvt::readPanelVendor(node, sysfs);
    check(identity.vendor == nvt::PanelVendor::Boe && identity.reported, "kernel BOE not read");
    check(nvt::readPanelVendor(tree.root / "dev/nvt-thp-spi0.0", sysfs).vendor == nvt::PanelVendor::Boe,
          "direct misc node not resolved");
    check(nvt::describePanel(identity) == "THP panel: boe (kernel panel_vendor)", "BOE log line");
    tree.write(attribute, "auo\n");
    identity = nvt::readPanelVendor(node, sysfs);
    check(identity.vendor == nvt::PanelVendor::Csot && identity.unknown == "auo",
          "an unknown panel_vendor must fall back to CSOT");
    check(nvt::describePanel(identity).starts_with("THP panel error: kernel panel_vendor 'auo'"),
          "unknown panel_vendor not reported as an error");
    tree.write(attribute, "\n");
    check(nvt::readPanelVendor(node, sysfs).unknown == "(empty)", "empty panel_vendor not reported");

    check(!nvt::calibrationMismatch(nvt::PanelVendor::Csot, 4), "CSOT calibration flagged on CSOT");
    check(!nvt::calibrationMismatch(nvt::PanelVendor::Boe, 26), "BOE calibration flagged on BOE");
    check(!nvt::calibrationMismatch(nvt::PanelVendor::Boe, std::nullopt), "calibration without ID flagged");
    const auto wrong = nvt::calibrationMismatch(nvt::PanelVendor::Boe, 4);
    check(wrong && wrong->find("piano_nova_boe_thp_config.ini") != std::string::npos &&
          wrong->find("csot") != std::string::npos, "CSOT calibration on BOE not reported");
    check(nvt::calibrationMismatch(nvt::PanelVendor::Csot, 26).has_value(), "BOE calibration on CSOT not reported");
    check(nvt::calibrationMismatch(nvt::PanelVendor::Csot, 7).has_value(), "foreign calibration not reported");

    const auto value = calibration();
    nlohmann::json document = {{"schema", 1}, {"rows", nvt::kRows}, {"columns", nvt::kColumns},
                               {"units_per_pixel", nvt::kSuperResolution},
                               {"calibration", {{"row_map", value.row_map}, {"column_map", value.column_map},
                                                {"row_maximum", value.row_maximum},
                                                {"column_maximum", value.column_maximum},
                                                {"edge_base", value.edge_base}, {"edge_size", value.edge_size},
                                                {"edge_gain", value.edge_gain}}}};
    tree.write("old.json", document.dump());
    check(!nvt::readCalibrationFile(tree.root / "old.json").project_vendor_id, "older calibration gained an ID");
    document["project_vendor_id"] = 26;
    tree.write("boe.json", document.dump());
    const auto file = nvt::readCalibrationFile(tree.root / "boe.json");
    check(file.project_vendor_id == 26 && file.calibration.row_maximum == value.row_maximum,
          "calibration project_vendor_id not read");
}

void stock(const char *configuration, const char *path) {
    struct Record { uint64_t sequence, timestamp; std::array<uint8_t, 5192> payload; };
    std::vector<Record> records;
    std::array<uint8_t, 5216> data{};
    std::ifstream file(path, std::ios::binary);
    check(bool(file), "cannot open stock fixture");
    while (file.read(reinterpret_cast<char *>(data.data()), data.size())) {
        Record record{get(data, 8, 8), get(data, 0, 8), {}};
        std::copy(data.begin() + 24, data.end(), record.payload.begin());
        records.push_back(record);
    }
    check(file.eof() && !file.gcount() && records.size() == 10, "expected ten complete stock slots");
    std::sort(records.begin(), records.end(), [](const auto &a, const auto &b) { return a.sequence < b.sequence; });
    size_t contacts = 0;
    nvt::LivePipeline pipeline(nvt::loadCalibration(configuration), [&](auto slots) { contacts += slots.size(); });
    nvt::StreamReader reader;
    for (size_t index = 0; index < records.size(); ++index) {
        const auto &record = records[index];
        // Envelope and time conversion are synthetic; the actual payload bytes stay unchanged.
        const auto bytes = wire(record.payload, record.sequence,
                                record.timestamp - records[0].timestamp + 1000000000,
                                nvt::kStreamValid | (index == 0 ? nvt::kStreamEpoch : 0));
        for (size_t offset = 0; offset < bytes.size(); offset += 17)
            reader.feed(std::span(bytes).subspan(offset, std::min(size_t{17}, bytes.size() - offset)),
                        [&](const auto &value) { pipeline.feed(value, value.timestamp_ns); });
    }
    reader.finish();
    const auto &s = pipeline.stats;
    check(s.records == 10 && s.fingers == 10 && !s.invalid && s.baselines == 1 &&
          s.processed == 1 && !contacts, "actual idle payload replay differs from stock-ring replay");
    std::cout << "PASS: 10 actual idle payloads in synthetic NTP1 envelopes, one processed, zero contacts\n";
}
} // namespace

int main(int argc, char **argv) {
    try {
        if (argc != 1 && argc != 3)
            throw std::runtime_error("Usage: test-live [CALIBRATION.json STOCK_RECORDS.bin]");
        framing();
        midStreamEpoch();
        lifecycle();
        orientation();
        panel();
        std::cout << "PASS: synthetic NTP1 splits/bounds, contact release lifecycle, recorded corner and "
                     "swipe orientation, BOE orientation, kernel panel_vendor (csot, boe, absent, "
                     "unknown) and calibration panel check; not hardware evidence\n";
        if (argc == 3)
            stock(argv[1], argv[2]);
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
