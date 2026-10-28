// SPDX-License-Identifier: Apache-2.0
// Adapted from xiaomi-sheng-thp UInputTouch; see NOTICE.
#include "input.hpp"
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <linux/uinput.h>
#include <stdexcept>
#include <sys/ioctl.h>
#include <system_error>
#include <unistd.h>

namespace nvt {
namespace {
void setupAxis(int fd, unsigned code, int maximum) {
    uinput_abs_setup setup{};
    setup.code = code;
    setup.absinfo.maximum = maximum;
    if (ioctl(fd, UI_ABS_SETUP, &setup) < 0)
        throw std::system_error(errno, std::generic_category(), "UI_ABS_SETUP");
}

// The pen axes carry the native units of the panel, so the resolution tells
// the applications the physical size of the unit.
void setupAxis(int fd, unsigned code, int maximum, int minimum, int resolution) {
    uinput_abs_setup setup{};
    setup.code = code;
    setup.absinfo.maximum = maximum;
    setup.absinfo.minimum = minimum;
    setup.absinfo.resolution = resolution;
    if (ioctl(fd, UI_ABS_SETUP, &setup) < 0)
        throw std::system_error(errno, std::generic_category(), "UI_ABS_SETUP");
}

void writeEvents(int fd, std::span<const input_event> events) {
    while (!events.empty()) {
        const ssize_t written = write(fd, events.data(), events.size_bytes());
        if (written < 0 && errno == EINTR)
            continue;
        if (written < 0)
            throw std::system_error(errno, std::generic_category(), "write uinput");
        if (!written || written % sizeof(input_event))
            throw std::runtime_error("short non-event-aligned uinput write");
        events = events.subspan(written / sizeof(input_event));
    }
}
} // namespace

UInputTouch::UInputTouch(int width, int height)
    : maximum_x_(width > 0 ? width - 1 : 0), maximum_y_(height > 0 ? height - 1 : 0) {
    if (width <= 0 || height <= 0)
        throw std::invalid_argument("invalid touch dimensions");
    active_.fill(-1);
    ids_.fill(-1);
    events_.reserve(kFingerSlots * 8 + 3);
    fd_ = open("/dev/uinput", O_WRONLY | O_CLOEXEC);
    if (fd_ < 0)
        throw std::system_error(errno, std::generic_category(), "open /dev/uinput");
    try {
        for (unsigned type : {EV_KEY, EV_ABS})
            checkedIoctl(UI_SET_EVBIT, type);
        checkedIoctl(UI_SET_KEYBIT, BTN_TOUCH);
        checkedIoctl(UI_SET_KEYBIT, BTN_TOOL_FINGER);
        for (unsigned axis : {ABS_MT_SLOT, ABS_MT_TOUCH_MAJOR, ABS_MT_POSITION_X,
                              ABS_MT_POSITION_Y, ABS_MT_TOOL_TYPE, ABS_MT_TRACKING_ID,
                              ABS_MT_PRESSURE})
            checkedIoctl(UI_SET_ABSBIT, axis);
        checkedIoctl(UI_SET_PROPBIT, INPUT_PROP_DIRECT);
        uinput_setup setup{};
        setup.id.bustype = BUS_VIRTUAL;
        setup.id.vendor = 0x2717;
        setup.id.product = 0x3653;
        setup.id.version = 1;
        std::strncpy(setup.name, "Piano THP Touchscreen", sizeof(setup.name) - 1);
        if (ioctl(fd_, UI_DEV_SETUP, &setup) < 0)
            throw std::system_error(errno, std::generic_category(), "UI_DEV_SETUP");
        setupAxis(fd_, ABS_MT_SLOT, kFingerSlots - 1);
        setupAxis(fd_, ABS_MT_TOUCH_MAJOR, 255);
        setupAxis(fd_, ABS_MT_POSITION_X, maximum_x_);
        setupAxis(fd_, ABS_MT_POSITION_Y, maximum_y_);
        setupAxis(fd_, ABS_MT_TOOL_TYPE, MT_TOOL_PALM);
        setupAxis(fd_, ABS_MT_TRACKING_ID, 65535);
        setupAxis(fd_, ABS_MT_PRESSURE, 1);
        if (ioctl(fd_, UI_DEV_CREATE) < 0)
            throw std::system_error(errno, std::generic_category(), "UI_DEV_CREATE");
    } catch (...) {
        close(fd_);
        throw;
    }
}

UInputTouch::~UInputTouch() {
    if (fd_ >= 0) {
        try { report({}); } catch (...) {}
        ioctl(fd_, UI_DEV_DESTROY);
        close(fd_);
    }
}

void UInputTouch::checkedIoctl(unsigned long request, unsigned long value) {
    if (ioctl(fd_, request, value) < 0)
        throw std::system_error(errno, std::generic_category(), "uinput capability setup");
}

std::string UInputTouch::sysname() const {
    std::array<char, 128> name{};
    if (ioctl(fd_, UI_GET_SYSNAME(name.size()), name.data()) < 0)
        throw std::system_error(errno, std::generic_category(), "UI_GET_SYSNAME");
    return name.data();
}

int UInputTouch::allocateTrackingId() {
    while (std::find(ids_.begin(), ids_.end(), next_id_) != ids_.end())
        next_id_ = (next_id_ + 1) & 0xffff;
    const int id = next_id_;
    next_id_ = (next_id_ + 1) & 0xffff;
    return id;
}

UInputPen::UInputPen(int x_max, int y_max, int pressure_max, int tilt_min, int tilt_max,
                       int x_resolution, int y_resolution)
    : maximum_x_(x_max), maximum_y_(y_max), pressure_max_(pressure_max), tilt_min_(tilt_min),
      tilt_max_(tilt_max) {
    if (x_max <= 0 || y_max <= 0 || pressure_max <= 0 || tilt_max <= tilt_min)
        throw std::invalid_argument("invalid pen dimensions");
    events_.reserve(10);
    fd_ = open("/dev/uinput", O_WRONLY | O_CLOEXEC);
    if (fd_ < 0)
        throw std::system_error(errno, std::generic_category(), "open /dev/uinput");
    try {
        for (unsigned type : {EV_KEY, EV_ABS})
            checkedIoctl(UI_SET_EVBIT, type);
        checkedIoctl(UI_SET_KEYBIT, BTN_TOOL_PEN);
        checkedIoctl(UI_SET_KEYBIT, BTN_TOUCH);
        checkedIoctl(UI_SET_KEYBIT, BTN_STYLUS);
        checkedIoctl(UI_SET_PROPBIT, INPUT_PROP_DIRECT);
        uinput_setup setup{};
        setup.id.bustype = BUS_VIRTUAL;
        setup.id.vendor = 0x22;
        setup.id.product = 0x5081;
        setup.id.version = 1;
        std::strncpy(setup.name, "Piano Focus Pen Pro", sizeof(setup.name) - 1);
        if (ioctl(fd_, UI_DEV_SETUP, &setup) < 0)
            throw std::system_error(errno, std::generic_category(), "UI_DEV_SETUP");
        setupAxis(fd_, ABS_X, maximum_x_, 0, x_resolution);
        setupAxis(fd_, ABS_Y, maximum_y_, 0, y_resolution);
        setupAxis(fd_, ABS_PRESSURE, pressure_max_, 0, 0);
        setupAxis(fd_, ABS_TILT_X, tilt_max_, tilt_min_, 0);
        setupAxis(fd_, ABS_TILT_Y, tilt_max_, tilt_min_, 0);
        if (ioctl(fd_, UI_DEV_CREATE) < 0)
            throw std::system_error(errno, std::generic_category(), "UI_DEV_CREATE");
    } catch (...) {
        close(fd_);
        throw;
    }
}

UInputPen::~UInputPen() {
    if (fd_ >= 0) {
        try { report(false, false, false, {}); } catch (...) {}
        ioctl(fd_, UI_DEV_DESTROY);
        close(fd_);
    }
}

void UInputPen::checkedIoctl(unsigned long request, unsigned long value) {
    if (ioctl(fd_, request, value) < 0)
        throw std::system_error(errno, std::generic_category(), "uinput capability setup");
}

void UInputPen::report(bool proximity, bool contact, bool squeeze, const StylusOutput &output) {
    if (contact && !proximity)
        throw std::invalid_argument("a contact needs a pen in proximity");
    if (contact && !(output.valid & kStylusPressureValid))
        throw std::invalid_argument("a contact needs a pressure");
    events_.clear();
    auto event = [this](uint16_t type, uint16_t code, int value) {
        input_event input{};
        input.type = type;
        input.code = code;
        input.value = value;
        events_.push_back(input);
    };
    event(EV_KEY, BTN_TOUCH, contact);
    event(EV_KEY, BTN_TOOL_PEN, proximity);
    event(EV_KEY, BTN_STYLUS, proximity && squeeze);
    if (proximity) {
        if (!(output.valid & kStylusCoordinatesValid))
            throw std::invalid_argument("a pen in proximity needs coordinates");
        event(EV_ABS, ABS_X, std::clamp(output.x, 0, maximum_x_));
        event(EV_ABS, ABS_Y, std::clamp(output.y, 0, maximum_y_));
    }
    if (output.valid & kStylusPressureValid)
        event(EV_ABS, ABS_PRESSURE, std::clamp(output.pressure, 0, pressure_max_));
    if (proximity && (output.valid & kStylusTiltValid)) {
        event(EV_ABS, ABS_TILT_X, std::clamp(output.tilt_x, tilt_min_, tilt_max_));
        event(EV_ABS, ABS_TILT_Y, std::clamp(output.tilt_y, tilt_min_, tilt_max_));
    }
    event(EV_SYN, SYN_REPORT, 0);
    writeEvents(fd_, events_);
    tool_present_ = proximity;
    contact_present_ = contact;
}

std::string UInputPen::sysname() const {
    std::array<char, 128> name{};
    if (ioctl(fd_, UI_GET_SYSNAME(name.size()), name.data()) < 0)
        throw std::system_error(errno, std::generic_category(), "UI_GET_SYSNAME");
    return name.data();
}

void UInputTouch::report(std::span<const Slot> slots) {
    std::array<const Slot *, kFingerSlots> visible{};
    for (const auto &slot : slots) {
        if (slot.number < 0 || slot.number >= kFingerSlots || slot.tracking_id < 0 || visible[slot.number])
            throw std::invalid_argument("invalid or duplicate touch slot");
        visible[slot.number] = &slot;
    }
    events_.clear();
    auto event = [this](uint16_t type, uint16_t code, int value) {
        input_event input{};
        input.type = type;
        input.code = code;
        input.value = value;
        events_.push_back(input);
    };
    for (int number = 0; number < kFingerSlots; ++number) {
        if (active_[number] >= 0 && !visible[number]) {
            event(EV_ABS, ABS_MT_SLOT, number);
            event(EV_ABS, ABS_MT_TRACKING_ID, -1);
            active_[number] = -1;
            ids_[number] = -1;
        }
    }
    for (int number = 0; number < kFingerSlots; ++number) {
        const auto *slot = visible[number];
        if (!slot)
            continue;
        event(EV_ABS, ABS_MT_SLOT, number);
        if (active_[number] != slot->tracking_id) {
            ids_[number] = allocateTrackingId();
            active_[number] = slot->tracking_id;
            event(EV_ABS, ABS_MT_TRACKING_ID, ids_[number]);
        }
        event(EV_ABS, ABS_MT_POSITION_X, std::clamp(slot->contact.x, 0, maximum_x_));
        event(EV_ABS, ABS_MT_POSITION_Y, std::clamp(slot->contact.y, 0, maximum_y_));
        event(EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_FINGER);
        event(EV_ABS, ABS_MT_TOUCH_MAJOR, std::clamp(slot->contact.area, 0, 255));
        event(EV_ABS, ABS_MT_PRESSURE, 1);
    }
    event(EV_KEY, BTN_TOUCH, !slots.empty());
    event(EV_KEY, BTN_TOOL_FINGER, !slots.empty());
    event(EV_SYN, SYN_REPORT, 0);
    writeEvents(fd_, events_);
}
} // namespace nvt
