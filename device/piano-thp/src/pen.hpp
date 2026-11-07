// SPDX-License-Identifier: Apache-2.0
// Adapted from the pen owner of xiaomi-sheng-thp; see NOTICE.
#pragma once
#include "frame.hpp"
#include "nvt_stylus_core.hpp"
#include <cstdint>
#include <string>

namespace nvt {

// The pressure of the pen arrives in the Bluetooth report5 and the squeeze of
// the barrel button in the report2 of the same device. A report of another
// kind belongs to another device and is not consumed here.
class PenPressureSource {
public:
    // Feeds one Bluetooth report. Returns false when the report carries
    // neither a pressure nor a squeeze.
    bool feed(const uint8_t *data, size_t bytes, uint64_t timestamp_ns, std::string *error);
    bool squeeze() const { return squeeze_; }
    bool havePressure() const { return have_pressure_; }
    uint64_t pressureTime() const { return pressure_time_; }
    void reset();

private:
    bool squeeze_ = false;
    bool have_pressure_ = false;
    uint64_t pressure_time_ = 0;
};

// The state of the pen in the axes of the panel.
struct PenState {
    bool proximity = false;
    bool contact = false;
    bool squeeze = false;
    StylusOutput output;
};

// The pen pipeline: the frames of the touch stream, the pressure of the
// Bluetooth reports and the tablet tool.
//
// The pressure freshness limit is a choice of the caller. A missing or an
// expired pressure releases the contact; it never becomes a pressure of
// zero, which would look like a click of the tip.
class PenPipeline {
public:
    PenPipeline(const StylusCalibration &calibration, uint64_t pressure_max_age_ns);

    // Feeds a pen frame of the touch stream and returns the new state.
    const PenState &feed(const PenFrame &frame, uint64_t timestamp_ns);
    // Feeds a Bluetooth report of the pen.
    bool feedReport(const uint8_t *data, size_t bytes, uint64_t timestamp_ns);
    // Releases the contact and the tool when the stream or the pressure is
    // stale. Returns true when the state changed.
    bool expire(uint64_t now_ns);

    const PenState &state() const { return state_; }
    const StylusCoreInfo &info() const { return info_; }
    uint64_t maxAge() const { return max_age_; }

private:
    std::unique_ptr<StylusCore> core_;
    PenPressureSource pressure_;
    PenState state_;
    StylusCoreInfo info_{};
    uint64_t max_age_ = 0;
    uint64_t last_frame_ns_ = 0;
    bool have_frame_ = false;
};

} // namespace nvt
