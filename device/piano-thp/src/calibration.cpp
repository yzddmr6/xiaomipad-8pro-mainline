// SPDX-License-Identifier: MIT
#include "calibration.hpp"
#include <fstream>
#include <nlohmann/json.hpp>
#include <stdexcept>

namespace nvt {
namespace {
int integer(const nlohmann::json &value) {
    if (!value.is_number_integer() || value < 0 || value > 1000000)
        throw std::runtime_error("calibration requires bounded nonnegative integers");
    return value.get<int>();
}
}

nlohmann::json readCalibrationDocument(const std::filesystem::path &path) {
    std::ifstream stream(path);
    if (!stream)
        throw std::runtime_error("cannot open calibration file");
    return nlohmann::json::parse(stream);
}

CalibrationFile readCalibrationFile(const std::filesystem::path &path) {
    const auto document = readCalibrationDocument(path);
    if (integer(document.at("schema")) != 1 ||
        integer(document.at("rows")) != kRows || integer(document.at("columns")) != kColumns ||
        integer(document.at("units_per_pixel")) != kSuperResolution)
        throw std::runtime_error("unsupported calibration schema or geometry");
    const auto &config = document.at("calibration");
    const auto &rows = config.at("row_map");
    const auto &columns = config.at("column_map");
    if (!rows.is_array() || !columns.is_array() || rows.size() != kRows || columns.size() != kColumns)
        throw std::runtime_error("electrode map length differs from geometry");
    Calibration calibration{};
    for (size_t index = 0; index < rows.size(); ++index)
        calibration.row_map[index] = integer(rows[index]);
    for (size_t index = 0; index < columns.size(); ++index)
        calibration.column_map[index] = integer(columns[index]);
    calibration.row_maximum = integer(config.at("row_maximum"));
    calibration.column_maximum = integer(config.at("column_maximum"));
    calibration.edge_base = integer(config.at("edge_base"));
    calibration.edge_size = integer(config.at("edge_size"));
    calibration.edge_gain = integer(config.at("edge_gain"));
    calibration.validate();
    std::optional<int> project_vendor_id;
    if (document.contains("project_vendor_id"))
        project_vendor_id = integer(document.at("project_vendor_id"));
    return {calibration, project_vendor_id};
}

namespace {
template <std::size_t N>
std::array<int, N> integerArray(const nlohmann::json &value) {
    if (!value.is_array() || value.size() != N)
        throw std::runtime_error("pen calibration array length differs from the geometry");
    std::array<int, N> result{};
    for (std::size_t index = 0; index < N; ++index)
        result[index] = integer(value[index]);
    return result;
}
} // namespace

StylusCalibration readStylusCalibration(const nlohmann::json &document) {
    if (!document.contains("pen"))
        throw std::runtime_error("the calibration file carries no pen facts");
    const auto &pen = document.at("pen");
    StylusCalibration calibration;
    calibration.coordinate_resolution = integer(pen.at("coordinate_resolution"));
    calibration.mapping_40 = integerArray<40>(pen.at("mapping_40"));
    calibration.mapping_60 = integerArray<60>(pen.at("mapping_60"));
    calibration.coordinate_differences = integerArray<6>(pen.at("coordinate_differences"));
    calibration.tilt_angles = integerArray<6>(pen.at("tilt_angles"));
    const auto enabled = pen.at("calibration_enabled");
    if (!enabled.is_boolean())
        throw std::runtime_error("pen calibration requires a boolean calibration flag");
    calibration.calibration_enabled = enabled.get<bool>();
    calibration.calibration_threshold = integer(pen.at("calibration_threshold"));
    calibration.calibration_rate = integer(pen.at("calibration_rate"));
    calibration.tip_slope_40 = integer(pen.at("tip_slope_40"));
    calibration.tip_slope_60 = integer(pen.at("tip_slope_60"));
    calibration.edge_40_left = integer(pen.at("edge_40_left"));
    calibration.edge_40_right = integer(pen.at("edge_40_right"));
    calibration.edge_60_left = integer(pen.at("edge_60_left"));
    calibration.edge_60_right = integer(pen.at("edge_60_right"));
    calibration.edge_limit_40_left = integer(pen.at("edge_limit_40_left"));
    calibration.edge_limit_40_right = integer(pen.at("edge_limit_40_right"));
    calibration.edge_limit_60_left = integer(pen.at("edge_limit_60_left"));
    calibration.edge_limit_60_right = integer(pen.at("edge_limit_60_right"));
    calibration.up_threshold = integer(pen.at("up_threshold"));
    calibration.ring_threshold = integer(pen.at("ring_threshold"));
    calibration.kalman_q_min = integer(pen.at("kalman_q_min"));
    calibration.kalman_q_max = integer(pen.at("kalman_q_max"));
    calibration.kalman_lambda_min = integer(pen.at("kalman_lambda_min"));
    calibration.kalman_lambda_max = integer(pen.at("kalman_lambda_max"));
    calibration.first_lock_dis = integer(pen.at("first_lock_dis"));
    calibration.stable_lock_dis = integer(pen.at("stable_lock_dis"));
    calibration.move_lock_dis = integer(pen.at("move_lock_dis"));
    return calibration;
}

Calibration loadCalibration(const std::filesystem::path &path) {
    return readCalibrationFile(path).calibration;
}
} // namespace nvt
