// SPDX-License-Identifier: MIT
// Synthetic pen frame fixtures only, never hardware touch evidence.
#include "frame.hpp"
#include "nvt_stylus_core.hpp"
#include "nvt_stylus_frame.hpp"

#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void check(bool condition, const char *message)
{
    if (!condition)
        throw std::runtime_error(message);
}

uint16_t le16(const uint8_t *p)
{
    return (uint16_t)p[0] | (uint16_t)p[1] << 8;
}

void put16(std::vector<uint8_t> &packet, std::size_t offset, uint16_t value)
{
    packet[offset] = value & 0xff;
    packet[offset + 1] = value >> 8;
}

void put32(std::vector<uint8_t> &packet, std::size_t offset, uint32_t value)
{
    for (int index = 0; index < 4; ++index)
        packet[offset + index] = (value >> (8 * index)) & 0xff;
}

uint32_t additiveSum(const std::vector<uint8_t> &packet, std::size_t begin, std::size_t end)
{
    uint32_t sum = 0;
    for (std::size_t offset = begin; offset < end; offset += 2)
        sum += le16(packet.data() + offset);
    return sum;
}

// Writes the additive checksum of the original packet: the sum of the 16-bit
// words of the range, its complement, and the complement of the word count.
void writeChecksum(std::vector<uint8_t> &packet, std::size_t header, std::size_t sumBegin,
                   std::size_t sumEnd, uint32_t count)
{
    const uint16_t expected = (uint16_t)(0u - additiveSum(packet, sumBegin, sumEnd));
    put16(packet, header + 4, expected);
    put16(packet, header + 12, (uint16_t)~expected);
    put32(packet, header + 16, ~count);
}

// Builds a type29 pen packet with the geometry of this panel: a 60 by 40
// main touch matrix, a 12 by 40 and a 60 by 8 stylus plane, 2400 nodes and a
// quarter of 1200 bytes.
std::vector<uint8_t> penPacket(unsigned part)
{
    constexpr std::size_t kNodes = 2400;
    constexpr std::size_t kPlane = 480;
    constexpr std::size_t kQuarterBytes = (kNodes / 4) * 2;
    constexpr std::size_t kTrailer = 100 + 4 * (kPlane * 2);
    constexpr std::size_t kHandStart = kTrailer + 20;
    constexpr std::size_t kRequired = kHandStart + kQuarterBytes;
    constexpr std::size_t kOuterCount = 1300;
    constexpr std::size_t kOuterLimit = 20 + kOuterCount * 4;
    constexpr std::size_t kPenCount = 980;
    constexpr std::size_t kPenLimit = 20 + kPenCount * 4;
    constexpr std::size_t kBytes = kOuterLimit;

    std::vector<uint8_t> packet(kBytes, 0);
    check(kBytes >= kRequired, "the synthetic packet is shorter than the decoder expects");
    packet[0] = 0xa5; /* the packet magic of the touch stream */
    packet[48] = 60;
    packet[49] = 40;
    packet[56] = 29; /* the pen data type */
    packet[64 + 7] = 12; /* the first stylus plane */
    packet[64 + 8] = 40;
    packet[64 + 9] = 60; /* the second stylus plane */
    packet[64 + 10] = 8;
    packet[64 + 11] = (uint8_t)part; /* the quarter of the main touch */
    put16(packet, 64 + 12, (uint16_t)kQuarterBytes);
    packet[64 + 17] = 0; /* the 120 Hz frame rate mode */
    packet[0x3d] = 0;

    // The pen trailer carries its own checksum header.
    put32(packet, kTrailer + 8, kPenCount);
    writeChecksum(packet, kTrailer, 20, kPenLimit, (uint32_t)kPenCount);

    // The outer checksum covers the whole packet, so it comes last.
    put32(packet, 8, (uint32_t)kOuterCount);
    writeChecksum(packet, 0, 20, kOuterLimit, (uint32_t)kOuterCount);
    return packet;
}

nvt::StylusCalibration calibration()
{
    nvt::StylusCalibration value;
    value.coordinate_resolution = 100;
    for (int row = 0; row < 40; ++row)
        value.mapping_40[row] = row * 53;
    for (int column = 0; column < 60; ++column)
        value.mapping_60[column] = column * 52;
    for (int index = 0; index < 6; ++index) {
        value.coordinate_differences[index] = index;
        value.tilt_angles[index] = index;
    }
    value.calibration_threshold = 0;
    value.calibration_rate = 1;
    value.tip_slope_40 = 8;
    value.tip_slope_60 = 8;
    value.up_threshold = 200;
    value.ring_threshold = 200;
    value.kalman_q_min = 1;
    value.kalman_q_max = 100;
    value.kalman_lambda_min = 1;
    value.kalman_lambda_max = 100;
    return value;
}

} // namespace

int main()
{
    try {
        // The original packet of the pen carries both checksums, and the
        // decoder accepts it.
        // The main touch arrives as four quarters, and the fourth one
        // completes the group.
        const char *error = nullptr;
        nvt::StylusFrame frame;
        const nvt::StylusGeometry geometry{ 12, 40, 60, 8, 2400 };
        check(frame.init(geometry) == 0, "the frame buffers were not allocated");
        auto packet = penPacket(1);
        for (unsigned part = 1; part <= 4; ++part) {
            packet = penPacket(part);
            const int code = frame.decode(packet.data(), packet.size(), &error);
            check(code == 0, error && *error ? error : "the synthetic packet was rejected");
            if (part < 4)
                check(!frame.mutualComplete(),
                      "the main touch completed before the fourth quarter");
        }
        check(frame.mutualComplete(), "the fourth quarter did not complete the main touch");
        check(frame.frequencyRequest() == 0, "the frame rate request was not decoded");

        // The pen frame is a packet of the same layout, and the touch
        // process can tell it apart from the finger frame.
        const auto parsed = nvt::parsePenFrame(packet);
        check(parsed.payload.size() == packet.size(), "the pen frame lost its payload");
        bool rejected = false;
        try {
            auto finger = packet;
            finger[56] = 3;
            nvt::parsePenFrame(finger);
        } catch (const std::exception &) { rejected = true; }
        check(rejected, "a finger frame was taken for a pen frame");

        // The pen adapter does not convert the other data type.
        auto type17 = penPacket(1);
        type17[56] = 17;
        nvt::StylusFrame other;
        check(other.init(nvt::StylusGeometry{ 12, 40, 60, 8, 2400 }) == 0,
              "the frame buffers were not allocated");
        const char *why = nullptr;
        check(other.decode(type17.data(), type17.size(), &why) != 0,
              "a type17 packet was accepted");
        check(why != nullptr, "a rejected packet carries no reason");

        // A damaged payload fails the additive checksum.
        auto damaged = penPacket(1);
        damaged[200] ^= 0xff;
        nvt::StylusFrame broken;
        check(broken.init(nvt::StylusGeometry{ 12, 40, 60, 8, 2400 }) == 0,
              "the frame buffers were not allocated");
        check(broken.decode(damaged.data(), damaged.size(), &why) != 0,
              "a damaged packet was accepted");

        // The core takes the actual 15-byte report of the pressure.
        std::string message;
        auto core = nvt::StylusCore::create(calibration(), 3, 1, 100000000, &message);
        check(core != nullptr, message.c_str());
        uint8_t report[15] = { 5, 0x10, 0x27, 0 };
        check(core->report5(report, sizeof(report), 1000, &message) == 0, message.c_str());
        check(core->report5(report, 14, 2000, &message) != 0, "a short report was accepted");
        uint8_t wrong[15] = { 4 };
        check(core->report5(wrong, sizeof(wrong), 2000, &message) != 0,
              "a report with another id was accepted");
        check(core->report5(report, sizeof(report), 500, &message) != 0,
              "a report before the previous one was accepted");

        // The payload must have the geometry of this panel.
        nvt::StylusOutput output;
        auto wrong_geometry = penPacket(1);
        wrong_geometry[49] = 39;
        check(core->process29(wrong_geometry.data(), wrong_geometry.size(), 3000, &output,
                              &message) != 0,
              "a payload with another geometry was accepted");
        check(output.valid == 0, "a rejected payload published values");

        std::cout << "stylus frame and core checks passed\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "stylus check failed: " << e.what() << "\n";
        return 1;
    }
}
