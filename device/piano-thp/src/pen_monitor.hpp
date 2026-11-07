// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <optional>
#include <string>

namespace nvt {
// Watches device creation/removal and the dock switch; there is no timer.
class PenMonitor {
public:
    PenMonitor();
    ~PenMonitor();
    PenMonitor(const PenMonitor &) = delete;
    PenMonitor &operator=(const PenMonitor &) = delete;
    int fd() const { return fd_; }
    int dockFd() const { return dock_fd_; }
    std::optional<bool> docked() const { return docked_; }
    bool changes();
    void readDock();
    static std::optional<std::string> hidraw();
private:
    void openDock();
    void dockState();
    int fd_ = -1, input_watch_ = -1, dock_fd_ = -1;
    std::optional<bool> docked_;
};

constexpr unsigned penScanModel(bool connected, std::optional<bool> docked, bool active) {
    return connected && docked.has_value() && !*docked && active ? 3 : 0;
}
}
