// SPDX-License-Identifier: MIT
#include "calibration.hpp"
#include <cmath>
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

double number(const nlohmann::json &value) {
    if (!value.is_number())
        throw std::runtime_error("panel transform requires numbers");
    return value.get<double>();
}
}

CalibrationFile readCalibrationFile(const std::filesystem::path &path) {
    std::ifstream stream(path);
    if (!stream)
        throw std::runtime_error("cannot open calibration file");
    const auto document = nlohmann::json::parse(stream);
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
    if (config.contains("panel_transform")) {
        const auto &transform = config.at("panel_transform");
        if (!transform.is_array() || transform.size() != 9)
            throw std::runtime_error("panel transform requires nine coefficients");
        for (size_t index = 0; index < 9; ++index)
            calibration.transform.h[index] = number(transform[index]);
        if (std::abs(calibration.transform.h[8]) < 1e-12)
            throw std::runtime_error("panel transform has a zero denominator");
        calibration.transform.enabled = true;
    }
    calibration.validate();
    std::optional<int> project_vendor_id;
    if (document.contains("project_vendor_id"))
        project_vendor_id = integer(document.at("project_vendor_id"));
    return {calibration, project_vendor_id};
}

Calibration loadCalibration(const std::filesystem::path &path) {
    return readCalibrationFile(path).calibration;
}
} // namespace nvt
