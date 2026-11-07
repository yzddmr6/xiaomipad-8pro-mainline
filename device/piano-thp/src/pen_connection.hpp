// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <map>
#include <string>
#include <systemd/sd-bus.h>

namespace nvt {
// BlueZ may retain a hidraw device across disconnects. Its existence alone
// cannot authorize scanning: observe actual Device1 state through D-Bus.
class PenConnection {
public:
    PenConnection();
    ~PenConnection();
    PenConnection(const PenConnection &) = delete;
    PenConnection &operator=(const PenConnection &) = delete;
    int fd() const;
    bool process();
    bool connected(const std::string &address) const;
private:
    friend struct PenConnectionTest;
    explicit PenConnection(std::nullptr_t) {}
    struct Device { std::string address; bool connected = false, resolved = false; };
    void query();
    void properties(sd_bus_message *, const std::string &path);
    void interfaces(sd_bus_message *, const std::string &path);
    void receive(sd_bus_message *);
    static int message(sd_bus_message *, void *, sd_bus_error *);
    sd_bus *bus_ = nullptr;
    sd_bus_slot *query_slot_ = nullptr;
    std::map<std::string, Device> devices_;
    bool changed_ = false, failed_ = false;
};
}
