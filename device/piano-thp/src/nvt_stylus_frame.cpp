// SPDX-License-Identifier: BSD-2-Clause-Patent
// Pinned HAL0cc145e5...: novatek_operation_v2 decode0x30718/core0x2fe44.
// Pointer layout is confirmed by actual ELF relocations and copies. See the
// original project's docs/devel/piano-pen-protocol.md for scope and the
// original MiCode references. This is CPU-only: no HAL startup, ioctl,
// frequency command or frame source.
#include "nvt_stylus_frame.hpp"

#include <cerrno>
#include <cstring>

namespace nvt {
namespace {

static_assert(sizeof(uintptr_t) == 8, "factory internal pen pointers are 64-bit");

uint16_t le16(const unsigned char *p)
{
    return (uint16_t)p[0] | (uint16_t)p[1] << 8;
}

uint32_t le32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

void putPointer(unsigned char *dst, const void *pointer)
{
    // HAL pointers are intentionally unaligned. Never cast dst to void **.
    uintptr_t value = (uintptr_t)pointer;
    std::memcpy(dst, &value, sizeof(value));
}

int reject(const char **error, const char *message)
{
    if (error)
        *error = message;
    return EINVAL;
}

bool checksum(const unsigned char *packet, std::size_t bytes, std::size_t header,
              std::size_t required_end, std::size_t maximum_end, std::size_t *end)
{
    if (header > bytes || bytes - header < 20)
        return false;
    const unsigned char *h = packet + header;
    uint32_t count = le32(h + 8);
    if (!count || count > INT32_MAX || count > (bytes - 20) / 4)
        return false;
    std::size_t limit = 20 + (std::size_t)count * 4;
    if (limit < required_end || limit > maximum_end)
        return false;
    uint16_t expected = le16(h + 4);
    if (le16(h + 12) != (uint16_t)~expected || le32(h + 16) != ~count)
        return false;
    uint32_t sum = 0;
    for (std::size_t i = 20; i < limit; i += 2)
        sum += le16(packet + i);
    if ((uint16_t)(0u - sum) != expected)
        return false;
    *end = limit;
    return true;
}

} // namespace

int StylusFrame::init(const StylusGeometry &geometry)
{
    std::size_t n1 = (std::size_t)geometry.col1 * geometry.row1;
    std::size_t n2 = (std::size_t)geometry.col2 * geometry.row2;
    // ALG stylus_total_data has two 28800-byte s32 matrix regions.
    if (!n1 || !n2 || n1 + n2 > 7200 || !geometry.nodes || geometry.nodes > 7200 ||
        geometry.nodes % 4)
        return EINVAL;

    geometry_ = geometry;
    n1_ = n1;
    n2_ = n2;
    frequency_request_ = -1;
    for (unsigned i = 0; i < 4; i++)
        matrices_[i].assign((i % 2 ? n2 : n1) * sizeof(int16_t) / sizeof(int16_t), 0);
    hand_.assign(geometry.nodes, 0);
    hand_parts_.assign(geometry.nodes, 0);
    return 0;
}

int StylusFrame::decode(const void *input, std::size_t bytes, const char **error)
{
    if (error)
        *error = nullptr;
    if (!input || hand_parts_.empty())
        return reject(error, "decoder has not been allocated");
    if (bytes < 100 || bytes > kStylusMaxPacket)
        return reject(error, "original SPI packet length outside capture bounds");
    const unsigned char *packet = (const unsigned char *)input, *metadata = packet + 64;
    if (packet[56] != 29)
        return reject(error,
                      "Novatek v2 pen adapter requires actual type29; type17 is not converted");
    const StylusGeometry &g = geometry_;
    if (metadata[7] != g.col1 || metadata[8] != g.row1 ||
        metadata[9] != g.col2 || metadata[10] != g.row2)
        return reject(error, "pen frame dimensions differ from actual HAL ini");
    if ((std::size_t)packet[48] * packet[49] != g.nodes)
        return reject(error, "main-touch node count differs from actual hardware config");
    std::size_t quarter = g.nodes / 4, quarter_bytes = quarter * 2;
    std::size_t trailer = 100 + 4 * (n1_ + n2_);
    std::size_t hand_start = trailer + 20, required = hand_start + quarter_bytes;
    if (required > bytes)
        return reject(error, "four pen matrices, trailer or hand quarter truncated");
    unsigned part = metadata[11];
    if (part < 1 || part > 4 || le16(metadata + 12) != quarter_bytes)
        return reject(error, "hand quarter number or length does not match v2 copy size");
    std::size_t outer_end = 0, pen_end = 0;
    if (!checksum(packet, bytes, 0, required, bytes, &outer_end))
        return reject(error, "outer original additive checksum, complement or length failed");
    // MiCode17/29 trailer advertises a second checksum from packet+20. Its
    // extent must cover all four arrays but stop before its checksum word.
    if (!checksum(packet, bytes, trailer, trailer, trailer + 4, &pen_end))
        return reject(error, "pen original additive checksum, complement or length failed");

    std::memset(frame_, 0, sizeof(frame_));
    std::memcpy(frame_ + 0x3c, packet, 64);
    std::memcpy(frame_ + 0x10d, metadata, 36);
    static const std::size_t pointers[4] = { 0x131, 0x139, 0x141, 0x149 };
    std::size_t cursor = 100;
    for (unsigned i = 0; i < 4; i++) {
        std::size_t elements = i % 2 ? n2_ : n1_;
        for (std::size_t j = 0; j < elements; j++)
            matrices_[i][j] = (int16_t)le16(packet + cursor + j * 2);
        putPointer(frame_ + pointers[i], matrices_[i].data());
        cursor += elements * 2;
    }
    std::memcpy(frame_ + 0x151, packet + trailer, 20);
    frame_[0x166] = 1;
    frame_[0x167] = 1;
    frame_[0x16a] = packet[24] == 4;
    putPointer(frame_ + 0x7c, hand_.data());
    // HAL30040..300c4 accumulates quarter indices and accepts sum10 on part4.
    // Track the actual populated quarters too: sum10 alone does not prove all
    // buffers were filled (for example 2,2,2,4). Never publish missing bytes.
    std::size_t offset = (part - 1) * quarter;
    for (std::size_t i = 0; i < quarter; i++)
        hand_parts_[offset + i] = (int16_t)le16(packet + hand_start + i * 2);
    packet_sum_ += part;
    part_mask_ |= 1u << (part - 1);
    if (part == 4) {
        if (packet_sum_ == 10 && part_mask_ == 15) {
            std::memcpy(hand_.data(), hand_parts_.data(), g.nodes * sizeof(int16_t));
            frame_[0x165] = 1;
        }
        packet_sum_ = 0;
        part_mask_ = 0;
    } else if (packet_sum_ > 10) {
        // A damaged or missing sequence cannot grow the accumulator unbounded.
        packet_sum_ = 0;
        part_mask_ = 0;
    }
    outer_end_ = outer_end;
    pen_end_ = pen_end;
    frequency_request_ = metadata[17] <= 63 && (metadata[17] & 15) <= 3 ? metadata[17] : -1;
    return 0;
}

} // namespace nvt
