// SPDX-License-Identifier: Apache-2.0
// Finger lifecycle adapted from xiaomi-sheng-thp; see NOTICE.
#pragma once
#include "frame.hpp"
#include "processor.hpp"
#include "stream.hpp"

namespace nvt {
class LivePipeline {
public:
    using Sink = std::function<void(std::span<const Slot>)>;
    using PenSink = std::function<void(const PenFrame &, uint64_t timestamp_ns)>;
    // Same 100 ms input-release policy as the reference service, not firmware retry timing.
    static constexpr uint64_t kStallNs = 100000000;

    LivePipeline(Calibration calibration, Sink sink, PenSink pen_sink = {})
        : processor_(std::move(calibration)), sink_(std::move(sink)),
          pen_sink_(std::move(pen_sink)) {}
    void feed(const StreamRecord &record, uint64_t now_ns);
    void expire(uint64_t now_ns);
    void reset();

    struct Statistics {
        uint64_t records = 0, fingers = 0, other_types = 0, invalid = 0, pen_frames = 0;
        uint64_t discontinuities = 0, baselines = 0, processed = 0, stalls = 0, stale = 0;
    } stats;

private:
    FingerProcessor processor_;
    Sink sink_;
    PenSink pen_sink_;
    uint64_t sequence_ = 0, timestamp_ns_ = 0, last_finger_ns_ = 0;
    bool active_ = false;
};
} // namespace nvt
