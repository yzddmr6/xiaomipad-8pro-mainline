// SPDX-License-Identifier: Apache-2.0
// Adapted from xiaomi-sheng-thp UInputTouch; see NOTICE.
#pragma once
#include "nvt_stylus_core.hpp"
#include "nvt_touch_core.hpp"
#include <linux/input.h>
#include <span>
#include <string>

namespace nvt {
class UInputTouch {
public:
    UInputTouch(int width, int height);
    ~UInputTouch();
    UInputTouch(const UInputTouch &) = delete;
    UInputTouch &operator=(const UInputTouch &) = delete;
    void report(std::span<const Slot> slots);
    std::string sysname() const;

private:
    void checkedIoctl(unsigned long request, unsigned long value);
    int allocateTrackingId();
    int fd_ = -1, maximum_x_, maximum_y_, next_id_ = 0;
    std::array<int, kFingerSlots> active_{}, ids_{};
    std::vector<input_event> events_;
};

// The tablet tool of the pen. The coordinates of the pen core are portrait
// native units; the caller rotates them into the landscape of the panel.
class UInputPen {
public:
    UInputPen(int x_max, int y_max, int pressure_max, int tilt_min, int tilt_max,
              int x_resolution, int y_resolution);
    ~UInputPen();
    UInputPen(const UInputPen &) = delete;
    UInputPen &operator=(const UInputPen &) = delete;
    // Reports the state of the pen. A field without its validity bit is
    // unknown and is not written, so a missing pressure never becomes a
    // pressure of zero.
    void report(bool proximity, bool contact, bool squeeze, const StylusOutput &output);
    std::string sysname() const;

private:
    void checkedIoctl(unsigned long request, unsigned long value);
    int fd_ = -1;
    int maximum_x_, maximum_y_, pressure_max_, tilt_min_, tilt_max_;
    bool tool_present_ = false, contact_present_ = false;
    std::vector<input_event> events_;
};
} // namespace nvt
