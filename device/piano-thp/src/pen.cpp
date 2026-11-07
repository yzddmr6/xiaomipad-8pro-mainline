// SPDX-License-Identifier: Apache-2.0
// Adapted from the pen owner of xiaomi-sheng-thp; see NOTICE.
#include "pen.hpp"

#include <algorithm>
#include <cstring>

namespace nvt {

void PenPressureSource::reset()
{
    squeeze_ = false;
    have_pressure_ = false;
    pressure_time_ = 0;
}

bool PenPressureSource::feed(const uint8_t *data, size_t bytes, uint64_t timestamp_ns,
                              std::string *error)
{
    if (!data || bytes < 2)
        return false;
    // The report5 of the pen carries the pressure, and the report2 the
    // squeeze of the barrel button. Both are reports of the same device.
    if (data[0] == 5) {
        if (bytes != 15) {
            if (error)
                *error = "the pressure report of the pen has another length";
            return false;
        }
        if (!have_pressure_ || timestamp_ns >= pressure_time_) {
            have_pressure_ = true;
            pressure_time_ = timestamp_ns;
        }
        return true;
    }
    if (data[0] == 2) {
        // The squeeze reports 6e while the button is held and 00 when it is
        // released.
        squeeze_ = data[1] == 0x6e;
        return true;
    }
    return false;
}

PenPipeline::PenPipeline(const StylusCalibration &calibration, uint64_t pressure_max_age_ns)
    : max_age_(pressure_max_age_ns)
{
    std::string error;
    core_ = StylusCore::create(calibration, 3, 1, pressure_max_age_ns, &error);
    if (!core_)
        throw std::runtime_error("cannot create the pen core: " + error);
    info_ = core_->info();
}

const PenState &PenPipeline::feed(const PenFrame &frame, uint64_t timestamp_ns)
{
    StylusOutput output;
    std::string error;
    if (core_->process29(frame.payload.data(), frame.payload.size(), timestamp_ns, &output,
                         &error))
        return state_;
    last_frame_ns_ = timestamp_ns;
    have_frame_ = true;

    // The core reports portrait native units; the panel is used in the
    // landscape, so the axes are exchanged and the vertical axis is mirrored.
    StylusOutput transformed = output;
    transformed.x = output.y;
    transformed.y = info_.x_max - output.x;
    const int tilt_x = output.tilt_y;
    transformed.tilt_y = -output.tilt_x;
    transformed.tilt_x = tilt_x;

    state_.output = transformed;
    state_.proximity = (output.valid & kStylusCoordinatesValid) != 0;
    state_.contact = false;
    if ((output.valid & kStylusPressureValid) && output.pressure_age_ns <= output.timestamp_ns) {
        const uint64_t pressure_time = output.timestamp_ns - output.pressure_age_ns;
        if (pressure_time <= timestamp_ns && timestamp_ns - pressure_time <= max_age_)
            state_.contact = state_.proximity && output.pressure > 0;
    }
    state_.squeeze = state_.proximity && pressure_.squeeze();
    return state_;
}

bool PenPipeline::feedReport(const uint8_t *data, size_t bytes, uint64_t timestamp_ns)
{
    std::string error;
    if (!pressure_.feed(data, bytes, timestamp_ns, &error))
        return false;
    if (data[0] == 5) {
        // The report is the actual 15-byte report including its id.
        std::string report_error;
        if (core_->report5(data, bytes, timestamp_ns, &report_error))
            return false;
    }
    return true;
}

bool PenPipeline::expire(uint64_t now_ns)
{
    bool changed = false;
    // The squeeze follows the reports, so it ends with the device.
    if (state_.squeeze && !pressure_.squeeze()) {
        state_.squeeze = false;
        changed = true;
    }
    if (state_.contact && pressure_.havePressure() &&
        now_ns >= pressure_.pressureTime() && now_ns - pressure_.pressureTime() > max_age_) {
        // The pressure expired: the contact ends without a pressure of zero.
        state_.contact = false;
        changed = true;
    }
    if (state_.proximity && have_frame_ && now_ns >= last_frame_ns_ &&
        now_ns - last_frame_ns_ > max_age_) {
        state_.proximity = false;
        state_.contact = false;
        state_.squeeze = false;
        changed = true;
    }
    return changed;
}

} // namespace nvt
