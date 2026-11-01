// SPDX-License-Identifier: MIT
#pragma once
#include "nvt_touch_core.hpp"
#include <filesystem>
#include <optional>

namespace nvt {
struct CalibrationFile {
    Calibration calibration;
    // Stock project_vendor_id of the source thp_config.ini, if the file records it.
    std::optional<int> project_vendor_id;
    // Absolute processor-to-panel Y mapping; absent keeps the panel default.
    std::optional<bool> invert_y;
};

CalibrationFile readCalibrationFile(const std::filesystem::path &path);
Calibration loadCalibration(const std::filesystem::path &path);
} // namespace nvt
