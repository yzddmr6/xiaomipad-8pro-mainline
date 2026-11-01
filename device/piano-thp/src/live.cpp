// SPDX-License-Identifier: Apache-2.0
// Finger-only event loop adapted from xiaomi-sheng-thp; see NOTICE.
#include "calibration.hpp"
#include "input.hpp"
#include "live_pipeline.hpp"
#include "orientation.hpp"
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <iostream>
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
        if (file.invert_y.has_value())
            std::cerr << "THP Y mapping: " << (*file.invert_y ? "inverted" : "direct")
                      << " (device calibration)\n";
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
        nvt::LivePipeline pipeline(calibration, [&](auto slots) {
            nvt::panelSlots(slots, calibration, vendor, panel_slots, file.invert_y);
            touch.report(panel_slots);
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
        pollfd watch{fd, POLLIN, 0};
        while (running) {
            const int result = poll(&watch, 1, 20);
            if (result < 0) {
                if (errno == EINTR)
                    continue;
                throw std::system_error(errno, std::generic_category(), "poll THP stream");
            }
            if (watch.revents & (POLLERR | POLLHUP | POLLNVAL))
                throw std::runtime_error("THP stream failed or was removed");
            if (watch.revents & POLLIN) {
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
                    pipeline.feed(record, bootTimeNs());
                });
            }
            pipeline.expire(bootTimeNs());
        }
        pipeline.reset();
        const auto &s = pipeline.stats;
        std::cerr << "records=" << s.records << " fingers=" << s.fingers
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
