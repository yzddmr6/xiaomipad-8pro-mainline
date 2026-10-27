// SPDX-License-Identifier: MIT
// Synthetic coordinate-regression fixtures only, never hardware touch evidence.
#include "calibration.hpp"
#include "processor.hpp"
#include <iostream>
#include <nlohmann/json.hpp>
#include <stdexcept>

namespace {
void check(bool condition, const char *message) {
    if (!condition)
        throw std::runtime_error(message);
}

nvt::Calibration calibration(int row_shift, int column_shift) {
    nvt::Calibration value{};
    for (int row = 0; row < nvt::kRows; ++row)
        value.row_map[row] = 10 * (row + 1) + row_shift;
    for (int column = 0; column < nvt::kColumns; ++column)
        value.column_map[column] = 10 * (column + 1) + column_shift;
    value.row_maximum = 10 * (10 * (nvt::kRows + 1) + row_shift);
    value.column_maximum = 10 * (10 * (nvt::kColumns + 1) + column_shift);
    value.edge_size = 100;
    value.edge_base = 1000;
    value.edge_gain = 1000;
    return value;
}

void exercise(const nvt::Calibration &value) {
    nvt::FingerProcessor processor(value);
    nvt::FingerFrame frame{0, 2, {}};
    frame.matrix.fill(10000);
    check(processor.feed(frame, 1000000000).baseline_ready, "reference not accepted");
    constexpr int row = 20, column = 30;
    for (int y = -1; y <= 1; ++y)
        for (int x = -1; x <= 1; ++x)
            frame.matrix[(row + y) * nvt::kColumns + column + x] +=
                x == 0 && y == 0 ? 1000 : (x == 0 || y == 0 ? 500 : 250);
    frame.scan_mode = 4;
    nvt::FingerProcessor::Update update;
    for (int index = 1; index <= 4; ++index) {
        frame.counter = index;
        update = processor.feed(frame, 1000000000 + index * 8333333);
    }
    check(update.result.has_value() && update.result->slots.size() == 1, "expected one synthetic contact");
    const auto &contact = update.result->slots[0].contact;
    check(contact.x == value.column_map[column] * nvt::kSuperResolution, "column calibration not used");
    check(contact.y == value.row_map[row] * nvt::kSuperResolution, "row calibration not used");
    processor.reset();
    frame.scan_mode = 2;
    frame.matrix.fill(10000);
    check(processor.feed(frame, 2000000000).baseline_ready, "epoch reset failed");
}
}

nvt::StylusCalibration stylusCalibration() {
    nlohmann::json pen;
    pen["coordinate_resolution"] = 100;
    pen["mapping_40"] = nlohmann::json::array();
    for (int row = 0; row < 40; ++row)
        pen["mapping_40"].push_back(row * 50);
    pen["mapping_60"] = nlohmann::json::array();
    for (int column = 0; column < 60; ++column)
        pen["mapping_60"].push_back(column * 40);
    pen["coordinate_differences"] = nlohmann::json::array({0, 28, 51, 67, 81, 87});
    pen["tilt_angles"] = nlohmann::json::array({0, 1500, 3000, 4500, 6000, 7000});
    pen["calibration_enabled"] = true;
    pen["calibration_threshold"] = 30;
    pen["calibration_rate"] = 6;
    pen["tip_slope_40"] = 8;
    pen["tip_slope_60"] = 7;
    pen["edge_40_left"] = 11;
    pen["edge_40_right"] = 12;
    pen["edge_60_left"] = 13;
    pen["edge_60_right"] = 14;
    pen["edge_limit_40_left"] = 21;
    pen["edge_limit_40_right"] = 22;
    pen["edge_limit_60_left"] = 23;
    pen["edge_limit_60_right"] = 24;
    pen["up_threshold"] = 200;
    pen["ring_threshold"] = 150;
    pen["kalman_q_min"] = 1;
    pen["kalman_q_max"] = 100;
    pen["kalman_lambda_min"] = 2;
    pen["kalman_lambda_max"] = 200;
    pen["first_lock_dis"] = 3;
    pen["stable_lock_dis"] = 4;
    pen["move_lock_dis"] = 5;
    nlohmann::json document;
    document["pen"] = pen;
    return nvt::readStylusCalibration(document);
}

void exerciseStylusCalibration() {
    const auto value = stylusCalibration();
    check(value.coordinate_resolution == 100, "the pen resolution was not read");
    check(value.mapping_40[39] == 1950, "the pen row map was not read");
    check(value.mapping_60[59] == 2360, "the pen column map was not read");
    check(value.coordinate_differences[5] == 87, "the pen differences were not read");
    check(value.tilt_angles[5] == 7000, "the pen angles were not read");
    check(value.calibration_enabled, "the pen calibration flag was not read");
    check(value.calibration_threshold == 30 && value.calibration_rate == 6,
          "the pen calibration numbers were not read");
    check(value.tip_slope_40 == 8 && value.tip_slope_60 == 7, "the pen slopes were not read");
    check(value.up_threshold == 200 && value.ring_threshold == 150,
          "the pen thresholds were not read");
    check(value.kalman_lambda_max == 200, "the pen filter numbers were not read");
    check(value.move_lock_dis == 5, "the pen lock distances were not read");

    bool rejected = false;
    try {
        nlohmann::json document;
        nvt::readStylusCalibration(document);
    } catch (const std::exception &) { rejected = true; }
    check(rejected, "a calibration file without pen facts was accepted");

    rejected = false;
    try {
        nlohmann::json document;
        document["pen"]["mapping_40"] = nlohmann::json::array({1, 2, 3});
        nvt::readStylusCalibration(document);
    } catch (const std::exception &) { rejected = true; }
    check(rejected, "a pen map with another length was accepted");
}

int main() {
    try {
        exerciseStylusCalibration();
        exercise(calibration(0, 0));
        exercise(calibration(13, 27));
        auto invalid = calibration(0, 0);
        invalid.row_map[1] = invalid.row_map[0];
        bool rejected = false;
        try { invalid.validate(); } catch (const std::invalid_argument &) { rejected = true; }
        check(rejected, "non-increasing map accepted");
        std::cout << "PASS: two synthetic calibrations, reset, invalid-map rejection, pen facts; not a hardware test\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
