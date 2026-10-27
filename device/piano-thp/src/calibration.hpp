// SPDX-License-Identifier: MIT
#pragma once
#include "nvt_stylus_core.hpp"
#include "nvt_touch_core.hpp"
#include <filesystem>
#include <nlohmann/json.hpp>
#include <optional>

namespace nvt {
struct CalibrationFile {
    Calibration calibration;
    // Stock project_vendor_id of the source thp_config.ini, if the file records it.
    std::optional<int> project_vendor_id;
};

nlohmann::json readCalibrationDocument(const std::filesystem::path &path);
CalibrationFile readCalibrationFile(const std::filesystem::path &path);
Calibration loadCalibration(const std::filesystem::path &path);

// The numeric pen facts of the same file. The extraction tool rejects an
// unsupported panel, so the values describe the panel of this unit.
StylusCalibration readStylusCalibration(const nlohmann::json &document);
} // namespace nvt
