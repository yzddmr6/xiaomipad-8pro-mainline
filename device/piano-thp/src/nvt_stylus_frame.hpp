// SPDX-License-Identifier: BSD-2-Clause-Patent
// Pinned HAL0cc145e5...: novatek_operation_v2 decode0x30718/core0x2fe44.
// Pointer layout is confirmed by actual ELF relocations and copies. See the
// original project's docs/devel/piano-pen-protocol.md for scope and the
// original MiCode references. This is CPU-only: no HAL startup, ioctl,
// frequency command or frame source.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace nvt {

constexpr std::size_t kStylusInternalBytes = 0x511;
constexpr std::size_t kStylusMaxPacket = 8192 - 257 - 1;

// The Novatek v2 configuration uses these four ini values and the actual
// hardware rx*tx count. No geometry is inferred from an absent pen frame.
struct StylusGeometry {
    uint8_t col1 = 0, row1 = 0, col2 = 0, row2 = 0;
    uint16_t nodes = 0;
};

// Only the pen prefix and its main-touch quarter accumulator are
// reconstructed. Other HAL fields, including SC pointers at 0x84/0x8c, are
// not provided. The frame and the embedded pointers remain valid until the
// next decode or until the object is destroyed.
class StylusFrame {
public:
    StylusFrame() = default;
    ~StylusFrame() = default;
    StylusFrame(const StylusFrame &) = delete;
    StylusFrame &operator=(const StylusFrame &) = delete;

    // Allocates the matrices and the main-touch quarters. Returns 0 on
    // success and an errno value otherwise.
    int init(const StylusGeometry &geometry);

    // Input is the original SPI frame_data_packet, with the record and the
    // SPI prefixes already removed. Both real additive checksums and
    // complements are required; the Android mmap CRC-rewritten input and
    // type17 are deliberately not normalized or bypassed. A non-NULL error
    // explains any rejected original packet.
    int decode(const void *input, std::size_t bytes, const char **error);

    const StylusGeometry &geometry() const { return geometry_; }
    int16_t *matrix(unsigned index) { return matrices_[index].data(); }
    int16_t *hand() { return hand_.data(); }
    int16_t *handParts() { return hand_parts_.data(); }
    const unsigned char *internalFrame() const { return frame_; }
    unsigned packetSum() const { return packet_sum_; }
    unsigned partMask() const { return part_mask_; }
    std::size_t outerEnd() const { return outer_end_; }
    std::size_t penEnd() const { return pen_end_; }
    int frequencyRequest() const { return frequency_request_; }
    bool mutualComplete() const { return frame_[0x165] != 0; }

    // Drops the quarter accumulator and the completion flag, so a rejected
    // solver frame cannot make a later hand group complete.
    void reset()
    {
        packet_sum_ = 0;
        part_mask_ = 0;
        frame_[0x165] = 0;
    }

private:
    StylusGeometry geometry_{};
    unsigned char frame_[kStylusInternalBytes]{};
    std::vector<int16_t> matrices_[4];
    std::vector<int16_t> hand_;
    std::vector<int16_t> hand_parts_;
    std::size_t n1_ = 0, n2_ = 0;
    unsigned packet_sum_ = 0, part_mask_ = 0;
    std::size_t outer_end_ = 0, pen_end_ = 0;
    int frequency_request_ = -1;
};

} // namespace nvt
