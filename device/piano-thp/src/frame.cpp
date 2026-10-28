// SPDX-License-Identifier: MIT
#include "frame.hpp"
#include <stdexcept>

namespace nvt {
namespace {
uint16_t le16(std::span<const uint8_t> data, size_t offset) {
    return uint16_t(data[offset]) | uint16_t(data[offset + 1]) << 8;
}

uint32_t le32(std::span<const uint8_t> data, size_t offset) {
    return uint32_t(le16(data, offset)) | uint32_t(le16(data, offset + 2)) << 16;
}
} // namespace

Packet parsePacket(std::span<const uint8_t> payload) {
    if (payload.size() < 64 || payload[0] != 0xa5)
        throw std::runtime_error("short or unrecognized THP packet");
    const uint32_t words = le32(payload, 8);
    const uint16_t checksum = le16(payload, 4);
    if (words > (payload.size() - 20) / 4 || words < (64 - 20) / 4 ||
        le32(payload, 16) != ~words || le16(payload, 12) != uint16_t(~checksum))
        throw std::runtime_error("invalid THP packet length/complements");
    uint32_t sum = checksum;
    for (size_t offset = 20; offset < 20 + size_t(words) * 4; offset += 2)
        sum += le16(payload, offset);
    if ((sum & 0xffff) != 0)
        throw std::runtime_error("THP payload checksum mismatch");
    return {payload, le16(payload, 2), payload[24], payload[56]};
}

FingerFrame parseFingerFrame(const Packet &packet) {
    const auto payload = packet.payload;
    if (payload.size() < 64 + kNodes * 2 || packet.data_type != 3 ||
        le32(payload, 8) != 0x4c3 || payload[48] != kColumns || payload[49] != kRows ||
        (packet.scan_mode != 2 && packet.scan_mode != 4))
        throw std::runtime_error("unsupported non-finger THP packet or geometry");
    FingerFrame frame{packet.counter, packet.scan_mode, {}};
    for (size_t index = 0; index < frame.matrix.size(); ++index) {
        const uint16_t value = le16(payload, 64 + index * 2);
        frame.matrix[index] = value < 0x8000 ? int(value) : int(value) - 0x10000;
    }
    return frame;
}

FingerFrame parseFingerFrame(std::span<const uint8_t> payload) {
    return parseFingerFrame(parsePacket(payload));
}

PenFrame parsePenFrame(const Packet &packet) {
    const auto payload = packet.payload;
    // The vendor adapter only carries the pen in the type29 frame; the other
    // data type stays as it is and is not converted.
    if (packet.data_type != 29)
        throw std::runtime_error("the packet does not carry a pen frame");
    if (payload.size() < 64 || payload[48] != kColumns || payload[49] != kRows)
        throw std::runtime_error("unsupported pen frame or geometry");
    return {payload, packet.counter, packet.scan_mode};
}

PenFrame parsePenFrame(std::span<const uint8_t> payload) {
    return parsePenFrame(parsePacket(payload));
}
} // namespace nvt
