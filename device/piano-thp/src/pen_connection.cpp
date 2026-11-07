// SPDX-License-Identifier: Apache-2.0
#include "pen_connection.hpp"
#include <algorithm>
#include <cctype>
#include <cstring>
#include <system_error>

namespace nvt {
namespace {
int checked(int value) {
    if (value < 0) throw std::system_error(-value, std::generic_category(), "BlueZ pen state");
    return value;
}
std::string normalized(std::string value) {
    for (char &c : value) c = std::toupper(static_cast<unsigned char>(c));
    return value;
}
}
PenConnection::PenConnection() {
    try {
        checked(sd_bus_open_system(&bus_));
        // A missing BlueZ service must not be activated or delay finger input.
        checked(sd_bus_add_match_async(bus_, nullptr,
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',"
            "member='PropertiesChanged',arg0='org.bluez.Device1'", message, nullptr, this));
        checked(sd_bus_add_match_async(bus_, nullptr,
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager'",
            message, nullptr, this));
        checked(sd_bus_add_match_async(bus_, nullptr,
            "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
            "member='NameOwnerChanged',arg0='org.bluez'", message, nullptr, this));
        query();
    } catch (...) { sd_bus_unref(bus_); bus_ = nullptr; throw; }
}
PenConnection::~PenConnection() { sd_bus_slot_unref(query_slot_); sd_bus_close_unref(bus_); }
int PenConnection::fd() const { return bus_ ? sd_bus_get_fd(bus_) : -1; }
void PenConnection::query() {
    query_slot_ = sd_bus_slot_unref(query_slot_);
    sd_bus_message *request = nullptr;
    checked(sd_bus_message_new_method_call(bus_, &request, "org.bluez", "/",
            "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"));
    sd_bus_message_set_auto_start(request, false);
    const int result = sd_bus_call_async(bus_, &query_slot_, request, message, this, 3000000);
    sd_bus_message_unref(request);
    checked(result);
}
void PenConnection::properties(sd_bus_message *m, const std::string &path) {
    auto &device = devices_[path];
    checked(sd_bus_message_enter_container(m, 'a', "{sv}"));
    while (checked(sd_bus_message_enter_container(m, 'e', "sv")) > 0) {
        const char *key, *contents;
        char type;
        checked(sd_bus_message_read(m, "s", &key));
        checked(sd_bus_message_peek_type(m, &type, &contents));
        if (!std::strcmp(key, "Address") && !std::strcmp(contents, "s")) {
            const char *value;
            checked(sd_bus_message_enter_container(m, 'v', "s"));
            checked(sd_bus_message_read(m, "s", &value));
            device.address = normalized(value);
            checked(sd_bus_message_exit_container(m));
        } else if ((!std::strcmp(key, "Connected") || !std::strcmp(key, "ServicesResolved")) &&
                   !std::strcmp(contents, "b")) {
            int value;
            checked(sd_bus_message_enter_container(m, 'v', "b"));
            checked(sd_bus_message_read(m, "b", &value));
            if (!std::strcmp(key, "Connected")) device.connected = value;
            else device.resolved = value;
            checked(sd_bus_message_exit_container(m));
        } else {
            checked(sd_bus_message_skip(m, "v"));
        }
        checked(sd_bus_message_exit_container(m));
    }
    checked(sd_bus_message_exit_container(m));
}
void PenConnection::interfaces(sd_bus_message *m, const std::string &path) {
    checked(sd_bus_message_enter_container(m, 'a', "{sa{sv}}"));
    while (checked(sd_bus_message_enter_container(m, 'e', "sa{sv}")) > 0) {
        const char *interface;
        checked(sd_bus_message_read(m, "s", &interface));
        if (!std::strcmp(interface, "org.bluez.Device1")) properties(m, path);
        else checked(sd_bus_message_skip(m, "a{sv}"));
        checked(sd_bus_message_exit_container(m));
    }
    checked(sd_bus_message_exit_container(m));
}
void PenConnection::receive(sd_bus_message *m) {
    changed_ = true;
    if (sd_bus_message_is_method_error(m, nullptr)) {
        devices_.clear();
        return;
    }
    uint8_t type;
    checked(sd_bus_message_get_type(m, &type));
    if (type == SD_BUS_MESSAGE_METHOD_RETURN) {
        checked(sd_bus_message_enter_container(m, 'a', "{oa{sa{sv}}}"));
        while (checked(sd_bus_message_enter_container(m, 'e', "oa{sa{sv}}")) > 0) {
            const char *path;
            checked(sd_bus_message_read(m, "o", &path));
            interfaces(m, path);
            checked(sd_bus_message_exit_container(m));
        }
        checked(sd_bus_message_exit_container(m));
    } else if (sd_bus_message_is_signal(m, "org.freedesktop.DBus.Properties", "PropertiesChanged")) {
        const char *interface, *invalid;
        checked(sd_bus_message_read(m, "s", &interface));
        const std::string path = sd_bus_message_get_path(m);
        properties(m, path);
        checked(sd_bus_message_enter_container(m, 'a', "s"));
        while (checked(sd_bus_message_read(m, "s", &invalid)) > 0)
            if (!std::strcmp(invalid, "Connected") || !std::strcmp(invalid, "ServicesResolved") ||
                !std::strcmp(invalid, "Address")) devices_[path].connected = false;
        checked(sd_bus_message_exit_container(m));
    } else if (sd_bus_message_is_signal(m, "org.freedesktop.DBus.ObjectManager", "InterfacesAdded")) {
        const char *path;
        checked(sd_bus_message_read(m, "o", &path));
        interfaces(m, path);
    } else if (sd_bus_message_is_signal(m, "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved")) {
        const char *path, *interface;
        checked(sd_bus_message_read(m, "o", &path));
        checked(sd_bus_message_enter_container(m, 'a', "s"));
        while (checked(sd_bus_message_read(m, "s", &interface)) > 0)
            if (!std::strcmp(interface, "org.bluez.Device1")) devices_.erase(path);
        checked(sd_bus_message_exit_container(m));
    } else if (sd_bus_message_is_signal(m, "org.freedesktop.DBus", "NameOwnerChanged")) {
        const char *name, *old_owner, *new_owner;
        checked(sd_bus_message_read(m, "sss", &name, &old_owner, &new_owner));
        query_slot_ = sd_bus_slot_unref(query_slot_);
        devices_.clear();
        if (*new_owner) query();
    }
}
int PenConnection::message(sd_bus_message *m, void *data, sd_bus_error *) {
    auto &self = *static_cast<PenConnection *>(data);
    try { self.receive(m); }
    catch (...) { self.devices_.clear(); self.failed_ = self.changed_ = true; }
    return 0;
}
bool PenConnection::process() {
    if (!bus_) return false;
    int result;
    do { result = sd_bus_process(bus_, nullptr); } while (result > 0 && !failed_);
    if (result < 0 || failed_) {
        devices_.clear();
        query_slot_ = sd_bus_slot_unref(query_slot_);
        bus_ = sd_bus_close_unref(bus_);
        changed_ = true;
    }
    const bool changed = changed_;
    changed_ = false;
    return changed;
}
bool PenConnection::connected(const std::string &address) const {
    const auto match = normalized(address);
    return std::any_of(devices_.begin(), devices_.end(), [&](const auto &entry) {
        return entry.second.address == match && entry.second.connected && entry.second.resolved;
    });
}
}
