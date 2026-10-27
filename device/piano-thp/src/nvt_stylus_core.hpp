// SPDX-License-Identifier: Apache-2.0
// Decoder adapted from ianchb/xiaomi-sheng-thp
// 34046210932d654a4c0df0121ecc31c008f8148c. The board calibration is
// supplied by the caller as numeric facts, so this file carries no original
// configuration of its own.
#pragma once

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace nvt {

// The numeric board facts of the pen, as extracted from the original
// configuration by the extraction tool. The tool rejects an unsupported
// geometry, so the values here already describe this panel.
struct StylusCalibration {
    int coordinate_resolution = 0;
    std::array<int, 40> mapping_40{};
    std::array<int, 60> mapping_60{};
    std::array<int, 6> coordinate_differences{};
    std::array<int, 6> tilt_angles{};
    bool calibration_enabled = false;
    int calibration_threshold = 0, calibration_rate = 0;
    int tip_slope_40 = 0, tip_slope_60 = 0;
    int edge_40_left = 0, edge_40_right = 0, edge_60_left = 0, edge_60_right = 0;
    int edge_limit_40_left = 0, edge_limit_40_right = 0;
    int edge_limit_60_left = 0, edge_limit_60_right = 0;
    int up_threshold = 0, ring_threshold = 0;
    int kalman_q_min = 0, kalman_q_max = 0;
    int kalman_lambda_min = 0, kalman_lambda_max = 0;
    int first_lock_dis = 0, stable_lock_dis = 0, move_lock_dis = 0;
};

struct StylusCoreInfo {
    int32_t orientation = 0, vendor_id = 0, resolution = 0;
    int32_t x_max = 0, y_max = 0, pressure_max = 0, tilt_min = 0, tilt_max = 0;
    int32_t pitch_x = 0, pitch_y = 0;
    int32_t calibration_threshold = 0, calibration_rate = 0;
    int32_t mapping_x_first = 0, mapping_x_last = 0;
    int32_t mapping_y_first = 0, mapping_y_last = 0;
};

enum StylusValid {
    kStylusRawValid = 1,
    kStylusCoordinatesValid = 2,
    kStylusTiltValid = 4,
    kStylusPressureValid = 8,
    kStylusRawDistanceValid = 16,
    kStylusMutualComplete = 32,
};

struct StylusOutput {
    uint64_t timestamp_ns = 0, pressure_age_ns = 0;
    uint32_t valid = 0;
    int32_t x = 0, y = 0, pressure = 0, raw_distance = 0, tilt_x = 0, tilt_y = 0;
    uint16_t frame_no = 0, raw_pressure = 0;
    int32_t frequency_request = 0;
};

// The pen core turns the actual type29 frames of the touch controller into
// coordinates and tilt, and pairs them with the pressure of the Bluetooth
// reports.
//
// The caller selects the actual touch LCDid profile, not the display label,
// and the P81c vendor3. The coordinates are portrait native units, not screen
// pixels. An unknown calibration is rejected.
class StylusCore {
public:
    ~StylusCore();

    // The pressure freshness limit is an explicit caller choice, not a value
    // of the original firmware.
    static std::unique_ptr<StylusCore> create(const StylusCalibration &calibration,
                                               int vendor_id, int orientation,
                                               uint64_t pressure_max_age_ns,
                                               std::string *error);

    void reset();
    const StylusCoreInfo &info() const { return info_; }

    // The BLE report5 must be the actual 15-byte report including the ID, not
    // the raw29 metadata or a made-up common record. Both timestamps must use
    // CLOCK_BOOTTIME: the driver timestamps the frames with the same clock.
    // Mixing them with CLOCK_MONOTONIC becomes incorrect across suspend.
    int report5(const uint8_t *report, size_t bytes, uint64_t timestamp_ns,
                std::string *error);

    // The payload begins at the original frame_data_packet, without the
    // 257-byte transport or the stream header. Fields without their validity
    // bit are unknown and must not be injected.
    int process29(const uint8_t *payload, size_t bytes, uint64_t timestamp_ns,
                  StylusOutput *output, std::string *error);

private:
    StylusCore();

    struct Pressure {
        uint64_t time = 0;
        int value = 0;
    };

    StylusCoreInfo info_{};
    std::unique_ptr<class StylusDecoder> decoder_;
    std::unique_ptr<class StylusFrame> frame_;
    std::vector<Pressure> pressure_;
    Pressure last_pressure_{};
    uint64_t max_age_ = 0, last_report_time_ = 0, last_frame_time_ = 0;
    bool have_pressure_ = false, have_report_time_ = false, have_frame_time_ = false;
};

} // namespace nvt
