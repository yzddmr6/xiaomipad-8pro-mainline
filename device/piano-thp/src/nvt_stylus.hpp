// SPDX-License-Identifier: Apache-2.0
// Derived from ianchb/xiaomi-sheng-thp, commit34046210932d654a4c0df0121ecc31c008f8148c.
// Piano changes: the board calibration arrives from the caller, the output is in
// portrait units, and the transport reader, the GPIO and the Bluetooth control, the
// uinput device and the calibration defaults of the original project are not part
// of this file.

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace nvt {

constexpr std::size_t kStylusPlaneNodes = 480;
constexpr std::size_t kStylusAxis40Nodes = 40;
constexpr std::size_t kStylusAxis60Nodes = 60;
constexpr std::size_t kStylusMutualRows = 40;
constexpr std::size_t kStylusMutualColumns = 60;
constexpr std::size_t kStylusMutualNodes =
    kStylusMutualRows * kStylusMutualColumns;
constexpr std::size_t kStylusMutualBlockNodes = 600;

using StylusMutualMatrix = std::array<int, kStylusMutualNodes>;

// All board calibration is loaded from the caller's actual Piano ini.
struct StylusProfile {
    int coordinate_resolution;
    std::array<int, 40> mapping_40;
    std::array<int, 60> mapping_60;
    int tip_pitch_40, tip_pitch_60;
    std::array<int, 6> coordinate_differences, tilt_angles;
    bool calibration_enabled;
    int calibration_threshold, calibration_rate;
    int tip_slope_40, tip_slope_60;
    int edge_40_left, edge_40_right, edge_60_left, edge_60_right;
    int edge_limit_40_left, edge_limit_40_right, edge_limit_60_left, edge_limit_60_right;
    int up_threshold, ring_threshold;
    int kalman_q_min, kalman_q_max, kalman_lambda_min, kalman_lambda_max;
    int first_lock_dis, stable_lock_dis, move_lock_dis;
};

struct RawStylusFrame {
    std::array<int, kStylusPlaneNodes> tip_x{};
    std::array<int, kStylusPlaneNodes> tip_y{};
    std::array<int, kStylusPlaneNodes> ring_x{};
    std::array<int, kStylusPlaneNodes> ring_y{};
    std::array<int, kStylusMutualBlockNodes> mutual_block_values{};
    int frame_interval = 120;
    std::uint8_t mutual_block = 0;
    bool special_state = false;
};

struct TipAxes {
    std::array<int, kStylusAxis40Nodes> axis_40{};
    std::array<int, kStylusAxis60Nodes> axis_60{};
    int energy_40 = 0;
    int energy_60 = 0;
};

struct RingAxes {
    std::array<int, kStylusAxis40Nodes> axis_40{};
    std::array<int, kStylusAxis60Nodes> axis_60{};
    int energy_40 = 0;
    int energy_60 = 0;
    bool valid = false;
};

struct StylusCoordinates {
    int tip_x = 0;
    int tip_y = 0;
    int ring_x = 0;
    int ring_y = 0;
    int difference_x = 0;
    int difference_y = 0;
    int tilt_x = 0;
    int tilt_y = 0;
};

struct StylusCoordinateState {
    int current_difference_x = 0;
    int current_difference_y = 0;
    int previous_difference_x = 0;
    int previous_difference_y = 0;
    int rejected_frames = 0;
    bool initialized = false;

    void rejectFrame();
    void acceptFrame();
};

struct TipGateState {
    bool require_reacquire = false;

    bool accept(int energy_40, int energy_60, int up_threshold);
};

struct StylusFrameResult {
    bool active = false;
    TipAxes tip;
    RingAxes ring;
    StylusCoordinates prefilter_coordinates;
    StylusCoordinates coordinates;
};

class StylusDecoder {
public:
    explicit StylusDecoder(const StylusProfile &profile);
    void reset();
    StylusFrameResult process(const RawStylusFrame &raw);

private:
    StylusProfile profile_;
    struct FilterPoint {
        int x = 0;
        int y = 0;
    };

    struct KalmanState {
        std::array<float, 4> state{};
        std::array<float, 16> covariance{};
        float covariance_scale = 1.0F;
        float residual_x = 0.0F;
        float residual_y = 0.0F;
        float process_noise = 0.01F;
        float previous_x = 0.0F;
        float previous_y = 0.0F;

        void reset();
    };

    struct FollowerState {
        int initial_x = 0;
        int initial_y = 0;
        int output_x = 0;
        int output_y = 0;
        int offset_x = 0;
        int offset_y = 0;
        int limit = 0;
        int previous_limit = 0;
        int raw_x = 0;
        int raw_y = 0;
        int age = 0;
        int average_x = 0;
        int average_y = 0;
        bool active = false;
        bool previous_active = false;

        void reset();
    };

    struct AxisHistory {
        std::array<int, kStylusAxis40Nodes> tip_40{};
        std::array<int, kStylusAxis60Nodes> tip_60{};
        std::array<int, kStylusAxis40Nodes> ring_40{};
        std::array<int, kStylusAxis60Nodes> ring_60{};
        int blend = 100;
    };

    TipGateState tip_gate_;
    StylusCoordinateState coordinate_state_;
    AxisHistory axis_history_;
    int status_ = 0;
    int previous_status_ = 0;
    int stylus_level_ = 0;
    KalmanState coordinate_kalman_;
    KalmanState tilt_kalman_;
    FollowerState coordinate_follower_;
    FollowerState tilt_follower_;

    void rejectFrame();
    void smoothAxes(StylusFrameResult &result, bool special_state);
    void advanceStatus(bool special_state);
    FilterPoint filterKalman(KalmanState &state, FilterPoint input,
                             int process_divisor,
                             int covariance_divisor) const;
    FilterPoint filterFollower(FollowerState &state, FilterPoint input,
                               int route, bool special_state,
                               int frame_interval) const;
    void applyFinalFilter(StylusCoordinates &coordinates, bool ring_valid,
                          bool special_state, int frame_interval);
};

void preprocessStylusInterference(RawStylusFrame &raw,
                                  const StylusMutualMatrix &touch_delta,
                                  bool ring_enabled = true);
void separateTipAxes(const RawStylusFrame &raw, TipAxes &output);
void removeTipBackground(TipAxes &axes);
void refineTipAxes(TipAxes &axes);
bool acceptTipEdges(const TipAxes &axes, const StylusProfile &profile);
void separateRingAxes(const RawStylusFrame &raw, RingAxes &output);
void removeRingBackground(RingAxes &axes, int threshold = 200);
StylusCoordinates calculateStylusCoordinates(const TipAxes &tip,
                                             const RingAxes &ring,
                                             StylusCoordinateState &state,
                                             const StylusProfile &profile);

}  // namespace nvt
