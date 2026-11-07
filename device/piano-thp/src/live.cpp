// SPDX-License-Identifier: Apache-2.0
// Touch event loop adapted from xiaomi-sheng-thp; see NOTICE.
#include "calibration.hpp"
#include "input.hpp"
#include "live_pipeline.hpp"
#include "orientation.hpp"
#include "pen.hpp"
#include "pen_monitor.hpp"
#include "pen_connection.hpp"
#include <linux/hidraw.h>
#include <sys/ioctl.h>
#include <cerrno>
#include <cmath>
#include <csignal>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <poll.h>
#include <sys/stat.h>
#include <system_error>
#include <time.h>
#include <unistd.h>

namespace {
volatile std::sig_atomic_t running = 1;
void stop(int) { running = 0; }

uint64_t bootTimeNs() {
    timespec value{};
    if (clock_gettime(CLOCK_BOOTTIME, &value) < 0)
        throw std::system_error(errno, std::generic_category(), "clock_gettime");
    return static_cast<uint64_t>(value.tv_sec) * 1000000000 + value.tv_nsec;
}

// The panel of this model measures 239 by 163 mm, so the native units of the
// pen are 1339 and 1310 per millimeter.
constexpr double kPanelWidthMm = 239.0;
constexpr double kPanelHeightMm = 163.0;

// The freshness limit of the pressure is a choice of this service and not a
// value of the original firmware.
constexpr uint64_t kPressureMaxAgeNs = 100000000;

// Fixed-width UAPI from linux/piano-touch.h in the matching kernel.
constexpr unsigned long kSetPen = _IOW('P', 0x20, uint32_t);
constexpr unsigned long kGetState = _IOR('P', 0x21, uint32_t);
constexpr uint32_t kStateActive = 1;

void recordBytes(int fd, std::span<const uint8_t> bytes) {
    while (!bytes.empty()) {
        const ssize_t written = write(fd, bytes.data(), bytes.size());
        if (written < 0 && errno == EINTR)
            continue;
        if (written < 0)
            throw std::system_error(errno, std::generic_category(), "record THP stream");
        if (!written)
            throw std::runtime_error("zero-length THP record write");
        bytes = bytes.subspan(written);
    }
}
}

int main(int argc, char **argv) {
    if (argc != 3 && (argc != 5 || std::string_view(argv[3]) != "--record")) {
        std::cerr << "Usage: piano-thp-live CALIBRATION.json /dev/nvt-thp-SPI_DEVICE [--record PRIVATE_FILE]\n"
                  << "Opening the stream downloads RAM firmware; use only with the matching kernel/board.\n";
        return 2;
    }
    int fd = -1, record_fd = -1;
    try {
        const auto file = nvt::readCalibrationFile(argv[1]);
        const auto &calibration = file.calibration;
        const auto panel_identity = nvt::readPanelVendor(argv[2]);
        const auto vendor = panel_identity.vendor;
        std::cerr << nvt::describePanel(panel_identity) << '\n';
        if (const auto mismatch = nvt::calibrationMismatch(vendor, file.project_vendor_id))
            std::cerr << "THP calibration error: " << *mismatch << '\n';
        if (argc == 5) {
            record_fd = open(argv[4], O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
            if (record_fd < 0)
                throw std::system_error(errno, std::generic_category(), "create private THP record");
        }
        const auto panel = nvt::panelAxes(calibration);
        nvt::UInputTouch touch(panel.width, panel.height);
        std::vector<nvt::Slot> panel_slots;
        std::optional<nvt::StylusCalibration> facts;
        std::unique_ptr<nvt::PenPipeline> pen;
        std::unique_ptr<nvt::UInputPen> pen_input;
        std::unique_ptr<nvt::PenMonitor> monitor;
        std::unique_ptr<nvt::PenConnection> connection;
        std::string pen_address;
        int pen_fd = -1;
        uint32_t scan_model = 0; // Kernel default; no command without a pen.
        const auto disable_pen = [&] {
            if (scan_model && fd >= 0) {
                uint32_t off = 0;
                if (ioctl(fd, kSetPen, &off) < 0)
                    std::cerr << "Pen scan disable request failed; fingers continue\n";
            }
            scan_model = 0;
            if (pen_fd >= 0) close(pen_fd);
            pen_fd = -1;
            pen_input.reset();
            pen.reset();
        };
        const auto pen_action = [&](auto action) {
            try { action(); }
            catch (const std::exception &error) {
                std::cerr << "Pen path disabled; fingers continue: " << error.what() << '\n';
                disable_pen();
            }
        };
        pen_action([&] {
            const auto document = nvt::readCalibrationDocument(argv[1]);
            if (document.contains("pen")) {
                facts = nvt::readStylusCalibration(document);
                monitor = std::make_unique<nvt::PenMonitor>();
            }
        });
        const auto report_pen = [&] {
            if (pen && pen_input) {
                const auto &state = pen->state();
                pen_input->report(state.proximity, state.contact, state.squeeze, state.output);
            }
        };
        const auto update_scan = [&] {
            uint32_t active = 0;
            if (pen_fd >= 0 && ioctl(fd, kGetState, &active) < 0)
                throw std::system_error(errno, std::generic_category(), "pen panel state");
            const uint32_t model = nvt::penScanModel(pen_fd >= 0 && bool(pen_input) &&
                connection && connection->connected(pen_address),
                monitor ? monitor->docked() : std::nullopt, active & kStateActive);
            if (model != scan_model) {
                // Preserve a failed enable request so the error path disables it.
                scan_model = model;
                if (ioctl(fd, kSetPen, &model) < 0)
                    throw std::system_error(errno, std::generic_category(), "pen scan request");
                if (!model && pen) {
                    pen->reset();
                    report_pen();
                }
            }
        };
        const auto open_pen = [&] {
            if (!facts || !monitor || pen_fd >= 0) return;
            const auto node = nvt::PenMonitor::hidraw();
            if (!node) return;
            // Do not subscribe to Bluetooth or query it on a pen-free system.
            if (!connection) connection = std::make_unique<nvt::PenConnection>();
            pen_fd = open(node->c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
            if (pen_fd < 0)
                throw std::system_error(errno, std::generic_category(), "open pen reports");
            hidraw_devinfo identity{};
            if (ioctl(pen_fd, HIDIOCGRAWINFO, &identity) < 0 ||
                identity.bustype != BUS_BLUETOOTH || identity.vendor != 0x22 || identity.product != 0x5081)
                throw std::runtime_error("pen report device identity changed");
            std::array<char, 128> address{};
            if (ioctl(pen_fd, HIDIOCGRAWUNIQ(address.size()), address.data()) < 0)
                throw std::system_error(errno, std::generic_category(), "pen connection identity");
            pen_address = address.data();
            if (!connection || !connection->connected(pen_address)) {
                close(pen_fd);
                pen_fd = -1;
                return;
            }
            pen = std::make_unique<nvt::PenPipeline>(*facts, kPressureMaxAgeNs);
            const auto &info = pen->info();
            pen_input = std::make_unique<nvt::UInputPen>(info.y_max, info.x_max, info.pressure_max,
                info.tilt_min, info.tilt_max, int(std::lround(info.y_max / kPanelWidthMm)),
                int(std::lround(info.x_max / kPanelHeightMm)));
            update_scan();
            std::cerr << "Pen input connected\n";
        };
        nvt::LivePipeline pipeline(calibration, [&](auto slots) {
            nvt::panelSlots(slots, calibration, vendor, panel_slots, file.invert_y);
            touch.report(panel_slots);
        }, [&](const nvt::PenFrame &frame, uint64_t timestamp_ns) {
            if (!pen || !scan_model) return;
            pen_action([&] { pen->feed(frame, timestamp_ns); report_pen(); });
        });
        nvt::StreamReader reader;
        std::signal(SIGINT, stop);
        std::signal(SIGTERM, stop);
        fd = open(argv[2], O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0)
            throw std::system_error(errno, std::generic_category(), "open THP stream");
        struct stat status{};
        if (fstat(fd, &status) < 0 || !S_ISCHR(status.st_mode))
            throw std::runtime_error("THP live input must be a character device");
        std::cerr << "THP stream opened; input=" << touch.sysname() << "; awaiting baseline\n";
        std::array<uint8_t, 16384> bytes{};
        std::array<uint8_t, 64> report{};
        pen_action(open_pen);
        pollfd watches[5]{};
        while (running) {
            watches[0] = {fd, short(POLLIN | (pen_fd >= 0 ? POLLPRI : 0)), 0};
            watches[1] = {pen_fd, POLLIN, 0};
            watches[2] = {monitor ? monitor->fd() : -1, POLLIN, 0};
            watches[3] = {monitor ? monitor->dockFd() : -1, POLLIN, 0};
            watches[4] = {connection ? connection->fd() : -1, POLLIN, 0};
            // Existing finger stall deadline; the pen adds no periodic timer.
            const int result = poll(watches, 5, 20);
            if (result < 0) {
                if (errno == EINTR)
                    continue;
                throw std::system_error(errno, std::generic_category(), "poll THP stream");
            }
            if (watches[0].revents & (POLLERR | POLLHUP | POLLNVAL))
                throw std::runtime_error("THP stream failed or was removed");
            pen_action([&] {
                // Pending D-Bus replies use the existing finger deadline.
                if (connection && connection->process()) {
                    if (pen_fd >= 0 && !connection->connected(pen_address)) disable_pen();
                    open_pen();
                    update_scan();
                }
                if (monitor && watches[2].revents) {
                    if (monitor->changes()) open_pen();
                    update_scan();
                }
                if (monitor && watches[3].revents) {
                    monitor->readDock();
                    update_scan();
                }
                if (watches[0].revents & POLLPRI) update_scan();
                if (pen_fd >= 0 && watches[1].revents) {
                    if (watches[1].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                        disable_pen();
                    } else if (watches[1].revents & POLLIN) {
                        const ssize_t size = read(pen_fd, report.data(), report.size());
                        if (!size || (size < 0 && errno != EAGAIN && errno != EINTR))
                            throw std::runtime_error("pen report device disconnected");
                        if (size > 0 && pen && scan_model) {
                            pen->feedReport(report.data(), size, bootTimeNs());
                            report_pen();
                        }
                    }
                }
            });
            if (watches[0].revents & POLLIN) {
                const ssize_t size = read(fd, bytes.data(), bytes.size());
                if (size < 0 && (errno == EAGAIN || errno == EINTR))
                    continue;
                if (size < 0)
                    throw std::system_error(errno, std::generic_category(), "read THP stream");
                if (!size) {
                    reader.finish();
                    throw std::runtime_error("THP stream ended");
                }
                if (record_fd >= 0)
                    recordBytes(record_fd, std::span(bytes.data(), size));
                reader.feed(std::span(bytes.data(), size), [&](const auto &record) {
                    if (pen && (record.flags & nvt::kStreamEpoch))
                        pen_action([&] { pen->reset(); report_pen(); });
                    pipeline.feed(record, bootTimeNs());
                });
            }
            pipeline.expire(bootTimeNs());
            pen_action([&] {
                if (pen && pen->expire(bootTimeNs())) report_pen();
            });
        }
        disable_pen();
        pipeline.reset();
        const auto &s = pipeline.stats;
        std::cerr << "records=" << s.records << " fingers=" << s.fingers
                  << " pen_frames=" << s.pen_frames
                  << " other_types=" << s.other_types << " invalid=" << s.invalid
                  << " discontinuities=" << s.discontinuities << " baselines=" << s.baselines
                  << " processed=" << s.processed << " stalls=" << s.stalls
                  << " stale=" << s.stale << '\n';
        close(fd);
        if (record_fd >= 0)
            close(record_fd);
        return 0;
    } catch (const std::exception &error) {
        if (fd >= 0)
            close(fd);
        if (record_fd >= 0)
            close(record_fd);
        std::cerr << error.what() << '\n';
        return 1;
    }
}
