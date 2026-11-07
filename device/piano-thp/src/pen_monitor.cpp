// SPDX-License-Identifier: Apache-2.0
#include "pen_monitor.hpp"
#include <array>
#include <cerrno>
#include <filesystem>
#include <fstream>
#include <fcntl.h>
#include <linux/input.h>
#include <sys/inotify.h>
#include <sys/ioctl.h>
#include <system_error>
#include <unistd.h>

namespace nvt {
PenMonitor::PenMonitor() {
    fd_ = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if (fd_ < 0)
        throw std::system_error(errno, std::generic_category(), "pen device monitor");
    if (inotify_add_watch(fd_, "/dev", IN_CREATE | IN_DELETE | IN_ATTRIB) < 0) {
        const int error = errno;
        close(fd_);
        throw std::system_error(error, std::generic_category(), "watch pen devices");
    }
    input_watch_ = inotify_add_watch(fd_, "/dev/input", IN_CREATE | IN_DELETE | IN_ATTRIB);
    try { openDock(); }
    catch (...) { close(fd_); fd_ = -1; throw; }
}
PenMonitor::~PenMonitor() {
    if (dock_fd_ >= 0) close(dock_fd_);
    if (fd_ >= 0) close(fd_);
}
std::optional<std::string> PenMonitor::hidraw() {
    std::error_code error;
    for (const auto &entry : std::filesystem::directory_iterator("/sys/class/hidraw", error)) {
        std::ifstream stream(entry.path() / "device/uevent");
        std::string line;
        while (std::getline(stream, line))
            if (line == "HID_ID=0005:00000022:00005081")
                return "/dev/" + entry.path().filename().string();
    }
    return std::nullopt;
}
void PenMonitor::dockState() {
    unsigned long switches = 0;
    if (ioctl(dock_fd_, EVIOCGSW(sizeof(switches)), &switches) < 0)
        docked_.reset();
    else
        docked_ = switches & (1UL << SW_PEN_INSERTED);
}
void PenMonitor::openDock() {
    if (dock_fd_ >= 0) return;
    std::error_code error;
    for (const auto &entry : std::filesystem::directory_iterator("/sys/class/input", error)) {
        if (!entry.path().filename().string().starts_with("event")) continue;
        std::ifstream stream(entry.path() / "device/name");
        std::string name;
        std::getline(stream, name);
        if (name != "Xiaomi Piano Pen Dock") continue;
        dock_fd_ = open(("/dev/input/" + entry.path().filename().string()).c_str(),
                        O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (dock_fd_ >= 0) dockState();
        return;
    }
}
bool PenMonitor::changes() {
    alignas(inotify_event) std::array<char, 8192> buffer{};
    bool hid = false, input = false;
    ssize_t size;
    while ((size = read(fd_, buffer.data(), buffer.size())) > 0) {
        for (size_t pos = 0; pos + sizeof(inotify_event) <= size_t(size);) {
            const auto *event = reinterpret_cast<const inotify_event *>(buffer.data() + pos);
            if (pos + sizeof(*event) + event->len > size_t(size)) break;
            if (event->mask & IN_Q_OVERFLOW) hid = input = true;
            if (event->mask & IN_IGNORED && event->wd == input_watch_) input_watch_ = -1;
            const std::string name = event->len ? event->name : "";
            hid |= name.starts_with("hidraw");
            input |= name == "input" || name.starts_with("event");
            pos += sizeof(*event) + event->len;
        }
    }
    if (size < 0 && errno != EAGAIN && errno != EINTR) {
        const int error = errno;
        close(fd_);
        fd_ = -1;
        throw std::system_error(error, std::generic_category(), "read pen device events");
    }
    if (input) {
        if (input_watch_ < 0)
            input_watch_ = inotify_add_watch(fd_, "/dev/input", IN_CREATE | IN_DELETE | IN_ATTRIB);
        openDock();
    }
    return hid;
}
void PenMonitor::readDock() {
    std::array<input_event, 16> events{};
    ssize_t size;
    while ((size = read(dock_fd_, events.data(), sizeof(events))) > 0) {
        for (size_t i = 0; i < size_t(size) / sizeof(input_event); ++i) {
            if (events[i].type == EV_SW && events[i].code == SW_PEN_INSERTED)
                docked_ = bool(events[i].value);
            if (events[i].type == EV_SYN && events[i].code == SYN_DROPPED)
                dockState();
        }
    }
    if (!size || (size < 0 && errno != EAGAIN && errno != EINTR)) {
        close(dock_fd_);
        dock_fd_ = -1;
        docked_.reset();
    }
}
}
