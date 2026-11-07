// SPDX-License-Identifier: MIT
#include "pen_connection.hpp"
#include <iostream>
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>

namespace {
void check(bool value, const char *message) {
    if (!value) throw std::runtime_error(message);
}
void ok(int result) { check(result >= 0, "synthetic D-Bus message failure"); }
}
namespace nvt {
struct PenConnectionTest {
    static void run() {
        PenConnection state(nullptr);
        sd_bus *bus = nullptr;
        int pair[2];
        check(socketpair(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0, pair) == 0,
              "socketpair failed");
        ok(sd_bus_new(&bus));
        ok(sd_bus_set_fd(bus, pair[0], pair[0]));
        ok(sd_bus_set_server(bus, 1, SD_ID128_NULL));
        ok(sd_bus_start(bus));
        const char *path = "/org/bluez/hci0/dev_synthetic";
        // Locally administered synthetic identity, never a device capture.
        const char *address = "02:00:00:00:00:01";
        auto change = [&](bool connected, bool resolved, bool invalidate) {
            sd_bus_message *m = nullptr;
            ok(sd_bus_message_new_signal(bus, &m, path,
                "org.freedesktop.DBus.Properties", "PropertiesChanged"));
            ok(sd_bus_message_append(m, "s", "org.bluez.Device1"));
            ok(sd_bus_message_open_container(m, 'a', "{sv}"));
            ok(sd_bus_message_append(m, "{sv}", "Address", "s", address));
            ok(sd_bus_message_append(m, "{sv}", "Connected", "b", int(connected)));
            ok(sd_bus_message_append(m, "{sv}", "ServicesResolved", "b", int(resolved)));
            ok(sd_bus_message_close_container(m));
            ok(sd_bus_message_open_container(m, 'a', "s"));
            if (invalidate) ok(sd_bus_message_append(m, "s", "Connected"));
            ok(sd_bus_message_close_container(m));
            ok(sd_bus_message_seal(m, 1, 0));
            ok(sd_bus_message_rewind(m, true));
            state.receive(m);
            sd_bus_message_unref(m);
        };
        check(!state.connected(address), "unknown pen was connected");
        change(true, true, false);
        check(state.connected(address), "connection signal was lost");
        change(false, true, false);
        check(!state.connected(address), "retained hidraw kept a disconnected pen active");
        change(true, false, false);
        check(!state.connected(address), "unresolved services authorized scanning");
        change(true, true, true);
        check(!state.connected(address), "invalidated connection authorized scanning");
        change(true, true, false);
        sd_bus_message *removed = nullptr;
        ok(sd_bus_message_new_signal(bus, &removed, "/",
            "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved"));
        ok(sd_bus_message_append(removed, "oas", path, 1, "org.bluez.Device1"));
        ok(sd_bus_message_seal(removed, 2, 0));
        ok(sd_bus_message_rewind(removed, true));
        state.receive(removed);
        check(!state.connected(address), "removed device authorized scanning");
        sd_bus_message_unref(removed);
        sd_bus_close_unref(bus);
        close(pair[1]);
    }
};
}
int main() {
    try {
        nvt::PenConnectionTest::run();
        std::cout << "PASS: BlueZ connection, retained hidraw disconnect, unresolved services, invalidation and removal\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
