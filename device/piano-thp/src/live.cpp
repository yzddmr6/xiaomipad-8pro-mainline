// SPDX-License-Identifier: Apache-2.0
// Finger-only event loop adapted from xiaomi-sheng-thp; see NOTICE.
#include "calibration.hpp"
#include "input.hpp"
#include "live_pipeline.hpp"
#include "orientation.hpp"
#include "pen.hpp"
#include <cerrno>
#include <cmath>
#include <csignal>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <poll.h>
#include <sys/stat.h>
#include <system_error>
#include <time.h>
#include <unistd.h>

namespace {
volatile std::sig_atomic_t running = 1;
void stop(int) { running = 0; }

uint64_t bootTimeNs() {
    timespec value{};
    if (clock_gettime(CLOCK_BOOTTIME, &value) < 0)
        throw std::system_error(errno, std::generic_category(), "clock_gettime");
    return static_cast<uint64_t>(value.tv_sec) * 1000000000 + value.tv_nsec;
}

// The panel of this model measures 239 by 163 mm, so the native units of the
// pen are 1339 and 1310 per millimeter.
constexpr double kPanelWidthMm = 239.0;
constexpr double kPanelHeightMm = 163.0;

// The freshness limit of the pressure is a choice of this service and not a
// value of the original firmware.
constexpr uint64_t kPressureMaxAgeNs = 100000000;

// Finds the hidraw node of the pen by its Bluetooth identifiers. The kernel
// keeps the private pressure report out of the input layer, but hidraw still
// receives it.
std::optional<std::string> findPenHidraw() {
    const std::filesystem::path root("/sys/class/hidraw");
    std::error_code error;
    for (const auto &entry : std::filesystem::directory_iterator(root, error)) {
        std::ifstream stream(entry.path() / "device" / "uevent");
        std::string line;
        while (std::getline(stream, line))
            if (line.rfind("HID_ID=", 0) == 0 && line.find(":00000022:00005081") != std::string::npos)
                return entry.path().filename().string();
    }
    return std::nullopt;
}

void recordBytes(int fd, std::span<const uint8_t> bytes) {
    while (!bytes.empty()) {
        const ssize_t written = write(fd, bytes.data(), bytes.size());
        if (written < 0 && errno == EINTR)
            continue;
        if (written < 0)
            throw std::system_error(errno, std::generic_category(), "record THP stream");
        if (!written)
            throw std::runtime_error("zero-length THP record write");
        bytes = bytes.subspan(written);
    }
}
}

int main(int argc, char **argv) {
    if (argc != 3 && (argc != 5 || std::string_view(argv[3]) != "--record")) {
        std::cerr << "Usage: piano-thp-live CALIBRATION.json /dev/nvt-thp-SPI_DEVICE [--record PRIVATE_FILE]\n"
                  << "Opening the stream downloads RAM firmware; use only with the matching kernel/board.\n";
        return 2;
    }
    int fd = -1, record_fd = -1;
    try {
        const auto file = nvt::readCalibrationFile(argv[1]);
        const auto &calibration = file.calibration;
        const auto panel_identity = nvt::readPanelVendor(argv[2]);
        const auto vendor = panel_identity.vendor;
        std::cerr << nvt::describePanel(panel_identity) << '\n';
        if (const auto mismatch = nvt::calibrationMismatch(vendor, file.project_vendor_id))
            std::cerr << "THP calibration error: " << *mismatch << '\n';
        if (argc == 5) {
            record_fd = open(argv[4], O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
            if (record_fd < 0)
                throw std::system_error(errno, std::generic_category(), "create private THP record");
        }
        const auto panel = nvt::panelAxes(calibration);
        nvt::UInputTouch touch(panel.width, panel.height);
        std::vector<nvt::Slot> panel_slots;
        // The pen path needs the pen facts of the same calibration file.
        std::unique_ptr<nvt::PenPipeline> pen;
        std::unique_ptr<nvt::UInputPen> pen_input;
        try {
            const auto facts = nvt::readStylusCalibration(nvt::readCalibrationDocument(argv[1]));
            pen = std::make_unique<nvt::PenPipeline>(facts, kPressureMaxAgeNs);
        } catch (const std::exception &error) {
            std::cerr << "THP calibration carries no pen facts, the pen path stays off: "
                      << error.what() << '\n';
        }
        const auto report_pen = [&] {
            if (!pen || !pen_input)
                return;
            const auto &state = pen->state();
            pen_input->report(state.proximity, state.contact, state.squeeze, state.output);
        };
        nvt::LivePipeline pipeline(calibration, [&](auto slots) {
            nvt::panelSlots(slots, calibration, vendor, panel_slots);
            touch.report(panel_slots);
        }, [&](const nvt::PenFrame &frame, uint64_t timestamp_ns) {
            if (!pen)
                return;
            pen->feed(frame, timestamp_ns);
            report_pen();
        });
        nvt::StreamReader reader;
        std::signal(SIGINT, stop);
        std::signal(SIGTERM, stop);
        fd = open(argv[2], O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0)
            throw std::system_error(errno, std::generic_category(), "open THP stream");
        struct stat status{};
        if (fstat(fd, &status) < 0 || !S_ISCHR(status.st_mode))
            throw std::runtime_error("THP live input must be a character device");
        std::cerr << "THP stream opened; input=" << touch.sysname() << "; awaiting baseline\n";
        std::array<uint8_t, 16384> bytes{};
        std::array<uint8_t, 64> report{};
        // The tablet tool of the pen exists while the pen is connected.
        int pen_fd = -1;
        const auto open_pen = [&] {
            if (!pen || pen_input)
                return;
            const auto node = findPenHidraw();
            if (!node)
                return;
            pen_fd = open(("/dev/" + *node).c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
            if (pen_fd < 0)
                return;
            const auto &info = pen->info();
            pen_input = std::make_unique<nvt::UInputPen>(info.y_max, info.x_max, info.pressure_max,
                                                         info.tilt_min, info.tilt_max,
                                                         int(std::lround(info.y_max / kPanelWidthMm)),
                                                         int(std::lround(info.x_max / kPanelHeightMm)));
            std::cerr << "Pen connected; input=" << pen_input->sysname() << '\n';
        };
        open_pen();
        pollfd watches[2]{};
        while (running) {
            watches[0] = {fd, POLLIN, 0};
            watches[1] = {pen_fd, POLLIN, 0};
            const int result = poll(watches, pen_fd >= 0 ? 2 : 1, 20);
            if (result < 0) {
                if (errno == EINTR)
                    continue;
                throw std::system_error(errno, std::generic_category(), "poll THP stream");
            }
            if (watches[0].revents & (POLLERR | POLLHUP | POLLNVAL))
                throw std::runtime_error("THP stream failed or was removed");
            if (pen_fd >= 0 && watches[1].revents) {
                if (watches[1].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                    // The pen left; the tablet tool goes with it.
                    close(pen_fd);
                    pen_fd = -1;
                    pen_input.reset();
                    std::cerr << "Pen disconnected\n";
                } else if (watches[1].revents & POLLIN) {
                    const ssize_t size = read(pen_fd, report.data(), report.size());
                    if (size > 0 && pen) {
                        pen->feedReport(report.data(), size, bootTimeNs());
                        report_pen();
                    }
                }
            }
            if (watches[0].revents & POLLIN) {
                const ssize_t size = read(fd, bytes.data(), bytes.size());
                if (size < 0 && (errno == EAGAIN || errno == EINTR))
                    continue;
                if (size < 0)
                    throw std::system_error(errno, std::generic_category(), "read THP stream");
                if (!size) {
                    reader.finish();
                    throw std::runtime_error("THP stream ended");
                }
                if (record_fd >= 0)
                    recordBytes(record_fd, std::span(bytes.data(), size));
                reader.feed(std::span(bytes.data(), size), [&](const auto &record) {
                    pipeline.feed(record, bootTimeNs());
                });
            }
            pipeline.expire(bootTimeNs());
            if (pen && pen->expire(bootTimeNs()))
                report_pen();
            if (pen && !pen_input)
                open_pen();
        }
        pipeline.reset();
        const auto &s = pipeline.stats;
        std::cerr << "records=" << s.records << " fingers=" << s.fingers
                  << " pen_frames=" << s.pen_frames
                  << " other_types=" << s.other_types << " invalid=" << s.invalid
                  << " discontinuities=" << s.discontinuities << " baselines=" << s.baselines
                  << " processed=" << s.processed << " stalls=" << s.stalls
                  << " stale=" << s.stale << '\n';
        close(fd);
        if (record_fd >= 0)
            close(record_fd);
        return 0;
    } catch (const std::exception &error) {
        if (fd >= 0)
            close(fd);
        if (record_fd >= 0)
            close(record_fd);
        std::cerr << error.what() << '\n';
        return 1;
    }
}
