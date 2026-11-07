// SPDX-License-Identifier: Apache-2.0
// Finger lifecycle adapted from xiaomi-sheng-thp; see NOTICE.
#include "live_pipeline.hpp"
#include <stdexcept>

namespace nvt {
void LivePipeline::reset() {
    if (active_)
        sink_({});
    active_ = false;
    processor_.reset();
    last_finger_ns_ = 0;
}

void LivePipeline::feed(const StreamRecord &record, uint64_t now_ns) {
    ++stats.records;
    if (!(record.flags & kStreamEpoch) && sequence_ &&
        (record.sequence <= sequence_ || record.timestamp_ns <= timestamp_ns_)) {
        ++stats.invalid;
        reset();
        return;
    }
    if ((record.flags & kStreamEpoch) || (sequence_ && record.sequence != sequence_ + 1)) {
        ++stats.discontinuities;
        reset();
    }
    sequence_ = record.sequence;
    timestamp_ns_ = record.timestamp_ns;
    if (now_ns < record.timestamp_ns || now_ns - record.timestamp_ns >= kStallNs) {
        ++stats.stale;
        reset();
        return;
    }
    std::optional<FingerFrame> finger;
    try {
        if (!(record.flags & kStreamValid) || !record.sequence || !record.timestamp_ns)
            throw std::runtime_error("invalid NTP1 record");
        const auto packet = parsePacket(record.payload);
        const auto checksum = uint16_t(record.payload[4] | uint16_t(record.payload[5]) << 8);
        uint32_t words = 0;
        for (size_t index = 0; index < 4; ++index)
            words |= uint32_t{record.payload[8 + index]} << (index * 8);
        if (record.checksum != checksum || record.length_words != words)
            throw std::runtime_error("NTP1 payload/header mismatch");
        if (packet.data_type == 29) {
            // The pen frame is handed over to the pen pipeline. The fingers
            // are not tracked at the same time, so a valid pen frame
            // releases them.
            const auto pen = parsePenFrame(packet);
            ++stats.pen_frames;
            if (active_) {
                sink_({});
                active_ = false;
            }
            if (pen_sink_)
                pen_sink_(pen, record.timestamp_ns);
            expire(now_ns);
            return;
        }
        if (packet.data_type != 3) {
            ++stats.other_types;
            expire(now_ns);
            return;
        }
        finger = parseFingerFrame(packet);
    } catch (const std::runtime_error &) {
        ++stats.invalid;
        reset();
        return;
    }
    ++stats.fingers;
    last_finger_ns_ = record.timestamp_ns;
    const auto update = processor_.feed(*finger, record.timestamp_ns);
    stats.baselines += update.baseline_ready;
    if (update.result) {
        ++stats.processed;
        sink_(update.result->slots);
        active_ = !update.result->slots.empty();
    }
}

void LivePipeline::expire(uint64_t now_ns) {
    if (active_ && (now_ns < last_finger_ns_ || now_ns - last_finger_ns_ >= kStallNs)) {
        ++stats.stalls;
        reset();
    }
}
} // namespace nvt
