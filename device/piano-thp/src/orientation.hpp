// SPDX-License-Identifier: MIT
#pragma once
#include "nvt_touch_core.hpp"
#include "panel.hpp"
#include <algorithm>
#include <span>
#include <vector>

namespace nvt {
// Piano panel orientation, measured with one-finger swipes on a CSOT unit held in
// landscape with the USB port on the right: a swipe from the left edge to the right
// edge increased processor X (electrode columns) at constant processor Y, and a swipe
// from the top edge to the bottom edge decreased processor Y (rows) at constant
// processor X. Processor X therefore spans the 3200-pixel width left to right and
// processor Y the 2136-pixel height with the top edge high. The CSOT panel reports
// X = processor X (range 0..column_maximum - 1) and Y = reversed processor Y
// (range 0..row_maximum - 1). The BOE default follows a unit whose processor Y
// already grows from the top edge to the bottom edge:
// liisoya/xiaomipad-8pro-mainline 8bbc68239c17 (MIT) validated its "flip-y" mode,
// which applies no flip at all, on a BOE unit. BOE therefore reports Y = processor Y.
// A per-device calibration override takes precedence over these defaults.
struct PanelAxes {
    int width;
    int height;
};

inline PanelAxes panelAxes(const Calibration &calibration) {
    return {calibration.column_maximum, calibration.row_maximum};
}

inline Contact panelContact(Contact contact, const Calibration &calibration, PanelVendor vendor,
                            std::optional<bool> invert_y = std::nullopt) {
    const int x = std::clamp(contact.x, 0, calibration.column_maximum - 1);
    const int y = std::clamp(contact.y, 0, calibration.row_maximum - 1);
    contact.x = x;
    contact.y = invert_y.value_or(vendor != PanelVendor::Boe) ? calibration.row_maximum - 1 - y : y;
    return contact;
}

inline void panelSlots(std::span<const Slot> slots, const Calibration &calibration, PanelVendor vendor,
                       std::vector<Slot> &output, std::optional<bool> invert_y = std::nullopt) {
    output.assign(slots.begin(), slots.end());
    for (Slot &slot : output)
        slot.contact = panelContact(slot.contact, calibration, vendor, invert_y);
}
} // namespace nvt
