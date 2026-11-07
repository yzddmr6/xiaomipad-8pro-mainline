// SPDX-License-Identifier: Apache-2.0
// Decoder adapted from ianchb/xiaomi-sheng-thp
// 34046210932d654a4c0df0121ecc31c008f8148c. The board calibration is
// supplied by the caller as numeric facts, so this file carries no original
// configuration of its own.
#include "nvt_stylus_core.hpp"

#include "nvt_stylus.hpp"
#include "nvt_stylus_frame.hpp"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <functional>
#include <stdexcept>
#include <vector>

namespace nvt {
namespace {

// The pen frame geometry of this panel: the main touch matrix is 60 columns
// by 40 rows, the stylus planes are 12 by 40 and 60 by 8.
constexpr StylusGeometry kGeometry{12, 40, 60, 8, 2400};

uint16_t le16(const uint8_t *p)
{
    return p[0] | uint16_t(p[1]) << 8;
}

int frameRate(uint8_t mode)
{
    constexpr int rates[] = { 120, 180, 240, 360, 60, 480, 144 };
    return mode < std::size(rates) ? rates[mode] : 0;
}

bool increasing(const std::array<int, 40> &map, int extent)
{
    if (map.front() < 0 || map.back() >= extent)
        return false;
    return std::adjacent_find(map.begin(), map.end(), std::greater_equal<int>()) == map.end();
}

bool increasing(const std::array<int, 60> &map, int extent)
{
    if (map.front() < 0 || map.back() >= extent)
        return false;
    return std::adjacent_find(map.begin(), map.end(), std::greater_equal<int>()) == map.end();
}

void buildProfile(const StylusCalibration &calibration, StylusProfile *profile)
{
    profile->coordinate_resolution = calibration.coordinate_resolution;
    profile->mapping_40 = calibration.mapping_40;
    profile->mapping_60 = calibration.mapping_60;
    profile->coordinate_differences = calibration.coordinate_differences;
    profile->tilt_angles = calibration.tilt_angles;
    profile->calibration_enabled = calibration.calibration_enabled;
    profile->calibration_threshold = calibration.calibration_threshold;
    profile->calibration_rate = calibration.calibration_rate;
    profile->tip_slope_40 = calibration.tip_slope_40;
    profile->tip_slope_60 = calibration.tip_slope_60;
    profile->edge_40_left = calibration.edge_40_left;
    profile->edge_40_right = calibration.edge_40_right;
    profile->edge_60_left = calibration.edge_60_left;
    profile->edge_60_right = calibration.edge_60_right;
    profile->edge_limit_40_left = calibration.edge_limit_40_left;
    profile->edge_limit_40_right = calibration.edge_limit_40_right;
    profile->edge_limit_60_left = calibration.edge_limit_60_left;
    profile->edge_limit_60_right = calibration.edge_limit_60_right;
    profile->up_threshold = calibration.up_threshold;
    profile->ring_threshold = calibration.ring_threshold;
    profile->kalman_q_min = calibration.kalman_q_min;
    profile->kalman_q_max = calibration.kalman_q_max;
    profile->kalman_lambda_min = calibration.kalman_lambda_min;
    profile->kalman_lambda_max = calibration.kalman_lambda_max;
    profile->first_lock_dis = calibration.first_lock_dis;
    profile->stable_lock_dis = calibration.stable_lock_dis;
    profile->move_lock_dis = calibration.move_lock_dis;

    // ALG886c0: the average pitch is (last-first)/(nodes-1), multiplied by the
    // resolution.
    profile->tip_pitch_40 =
        (profile->mapping_40.back() - profile->mapping_40.front()) / 39 *
        profile->coordinate_resolution;
    profile->tip_pitch_60 =
        (profile->mapping_60.back() - profile->mapping_60.front()) / 59 *
        profile->coordinate_resolution;
}

void validate(const StylusCalibration &calibration)
{
    if (calibration.coordinate_resolution <= 0)
        throw std::runtime_error("pen calibration requires a positive resolution");
    if (!increasing(calibration.mapping_40, 2136) ||
        !increasing(calibration.mapping_60, 3200))
        throw std::runtime_error("pen calibration mapping is outside the panel bounds");
    if (calibration.coordinate_differences.front() != 0 ||
        calibration.tilt_angles.front() != 0 ||
        std::adjacent_find(calibration.coordinate_differences.begin(),
                           calibration.coordinate_differences.end(),
                           std::greater_equal<int>()) != calibration.coordinate_differences.end() ||
        std::adjacent_find(calibration.tilt_angles.begin(), calibration.tilt_angles.end(),
                           std::greater_equal<int>()) != calibration.tilt_angles.end())
        throw std::runtime_error("pen calibration differences are not increasing");
    if (calibration.calibration_threshold < 0 || calibration.calibration_rate <= 0 ||
        calibration.tip_slope_40 <= 0 || calibration.tip_slope_40 >= 16 ||
        calibration.tip_slope_60 <= 0 || calibration.tip_slope_60 >= 16 ||
        calibration.up_threshold <= 0 || calibration.ring_threshold <= 0 ||
        calibration.kalman_q_min <= 0 || calibration.kalman_q_max <= 0 ||
        calibration.kalman_lambda_min <= 0 || calibration.kalman_lambda_max <= 0)
        throw std::runtime_error("pen calibration values are outside the supported range");
}

} // namespace

StylusCore::StylusCore() = default;
StylusCore::~StylusCore() = default;

std::unique_ptr<StylusCore> StylusCore::create(const StylusCalibration &calibration,
                                                int vendor_id, int orientation,
                                                uint64_t pressure_max_age_ns,
                                                std::string *error)
{
    try {
        if (orientation != 1 || vendor_id != 3)
            throw std::runtime_error("the pen core requires the portrait P81c vendor3 profile");

        validate(calibration);

        StylusProfile profile;
        buildProfile(calibration, &profile);

        auto core = std::unique_ptr<StylusCore>(new StylusCore());
        core->decoder_ = std::make_unique<StylusDecoder>(profile);
        core->frame_ = std::make_unique<StylusFrame>();
        if (core->frame_->init(kGeometry))
            throw std::runtime_error("cannot allocate the pen frame buffers");
        core->max_age_ = pressure_max_age_ns;

        const auto &p = profile;
        core->info_ = { orientation, vendor_id, p.coordinate_resolution, 213599, 319999, 16383,
                        -60, 60, p.tip_pitch_40, p.tip_pitch_60, p.calibration_threshold,
                        p.calibration_rate, p.mapping_40.front(), p.mapping_40.back(),
                        p.mapping_60.front(), p.mapping_60.back() };
        return core;
    } catch (const std::exception &e) {
        if (error)
            *error = e.what();
        return nullptr;
    }
}

void StylusCore::reset()
{
    decoder_->reset();
    pressure_.clear();
    have_pressure_ = false;
    have_report_time_ = false;
    have_frame_time_ = false;
}

int StylusCore::report5(const uint8_t *report, size_t bytes, uint64_t timestamp_ns,
                         std::string *error)
{
    if (!report || bytes != 15 || report[0] != 5) {
        if (error)
            *error = "expected an actual 15-byte BLE report5";
        return -EINVAL;
    }
    const int pressure = le16(report + 1);
    if (pressure > info_.pressure_max ||
        (have_report_time_ && timestamp_ns < last_report_time_)) {
        if (error)
            *error = "pressure value or report timestamp is outside the real input range";
        return -ERANGE;
    }
    try {
        pressure_.push_back({ timestamp_ns, pressure });
        if (pressure_.size() > 10)
            pressure_.erase(pressure_.begin());
    } catch (const std::exception &e) {
        if (error)
            *error = e.what();
        return -ENOMEM;
    }
    last_report_time_ = timestamp_ns;
    have_report_time_ = true;
    return 0;
}

int StylusCore::process29(const uint8_t *payload, size_t bytes, uint64_t timestamp_ns,
                           StylusOutput *output, std::string *error)
{
    if (!payload || !output)
        return -EINVAL;
    *output = {};
    if (have_frame_time_ && timestamp_ns < last_frame_time_) {
        if (error)
            *error = "frame timestamps must preserve the actual monotonic ordering";
        return -EINVAL;
    }
    if (bytes < 100 || payload[48] != 60 || payload[49] != 40) {
        if (error)
            *error = "the actual payload must have 60 columns and 40 rows";
        return -EINVAL;
    }
    // The rate validation is stateless and must precede the quarter
    // accumulation.
    const int rate = frameRate(payload[0x3d]);
    if (!rate) {
        if (error)
            *error = "unknown actual frame-rate mode";
        return -EOPNOTSUPP;
    }
    const char *why = nullptr;
    const int decoded = frame_->decode(payload, bytes, &why);
    if (decoded) {
        if (error)
            *error = why ? why : "the raw29 frame was rejected";
        return -decoded;
    }
    RawStylusFrame raw;
    for (size_t i = 0; i < 480; ++i) {
        raw.tip_x[i] = frame_->matrix(0)[i];
        raw.tip_y[i] = frame_->matrix(1)[i];
        raw.ring_x[i] = frame_->matrix(2)[i];
        raw.ring_y[i] = frame_->matrix(3)[i];
    }
    raw.frame_interval = rate;
    raw.special_state = payload[80] != 0;
    StylusFrameResult result;
    try {
        result = decoder_->process(raw);
    } catch (const std::exception &e) {
        // A rejected solver frame cannot make a later hand group complete.
        frame_->reset();
        decoder_->reset();
        if (error)
            *error = e.what();
        return -ERANGE;
    }
    last_frame_time_ = timestamp_ns;
    have_frame_time_ = true;
    output->timestamp_ns = timestamp_ns;
    output->valid = kStylusRawValid | kStylusRawDistanceValid;
    output->frame_no = le16(payload + 66);
    output->raw_pressure = le16(payload + 68);
    output->raw_distance = payload[80] != 0;
    output->frequency_request = frame_->frequencyRequest();
    if (frame_->mutualComplete())
        output->valid |= kStylusMutualComplete;
    if (result.active) {
        output->valid |= kStylusCoordinatesValid;
        output->x = std::clamp(result.coordinates.tip_x, 0, info_.x_max);
        output->y = std::clamp(result.coordinates.tip_y, 0, info_.y_max);
        if (result.ring.valid) {
            output->valid |= kStylusTiltValid;
            output->tilt_x = std::clamp(result.coordinates.tilt_x, info_.tilt_min, info_.tilt_max);
            output->tilt_y = std::clamp(result.coordinates.tilt_y, info_.tilt_min, info_.tilt_max);
        }
    }
    // Consume one actual report per raw frame, as the original bounded queue.
    if (!pressure_.empty() && pressure_.front().time <= timestamp_ns) {
        last_pressure_ = pressure_.front();
        pressure_.erase(pressure_.begin());
        have_pressure_ = true;
    }
    if (have_pressure_ && last_pressure_.time <= timestamp_ns) {
        output->pressure_age_ns = timestamp_ns - last_pressure_.time;
        if (output->pressure_age_ns <= max_age_) {
            output->pressure = last_pressure_.value;
            output->valid |= kStylusPressureValid;
        }
    }
    return 0;
}

} // namespace nvt
