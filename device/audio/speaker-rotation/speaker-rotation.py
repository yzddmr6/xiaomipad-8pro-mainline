#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Map stereo playback to the tablet's four speaker positions as GNOME rotates."""
import json
import subprocess
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

BUS = "org.gnome.Mutter.DisplayConfig"
PATH = "/org/gnome/Mutter/DisplayConfig"
IFACE = "org.gnome.Mutter.DisplayConfig"

# Route tables are part of each board's map file: the service owns the generic
# Mutter/PipeWire behavior while board packages describe physical PCM outputs.


class SpeakerRotation:
    def __init__(self, config):
        self.config = config
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.proxy = Gio.DBusProxy.new_sync(
            self.connection, Gio.DBusProxyFlags.NONE, None, BUS, PATH, IFACE, None
        )
        self.last_transform = None
        self.last_node = None
        # Reapply after PipeWire recreates filter nodes (including suspend).
        self.pw = Gio.Subprocess.new(['pw-dump', '--monitor'], Gio.SubprocessFlags.STDOUT_PIPE)
        self.events = Gio.DataInputStream.new(self.pw.get_stdout_pipe())
        self.watch_pipewire()
        self.connection.signal_subscribe(
            BUS, IFACE, "MonitorsChanged", PATH, None,
            Gio.DBusSignalFlags.NONE, self.on_monitors_changed, None
        )

    def watch_pipewire(self):
        self.events.read_line_async(GLib.PRIORITY_DEFAULT, None, self.pipewire_changed)

    def pipewire_changed(self, stream, result):
        try:
            line, _length = stream.read_line_finish(result)
            if line is None:
                return
            # pw-dump also reports unrelated nodes and playback state changes.
            # React only when the filter itself is announced.
            key, _, value = line.strip().partition(b':')
            if (key == b'"node.name"' and
                    value.strip().rstrip(b',') == json.dumps(self.config["control_node"]).encode()):
                self.update()
            self.watch_pipewire()
        except GLib.Error:
            pass

    def on_monitors_changed(self, *_args):
        self.update()

    def internal_transform(self):
        reply = self.proxy.call_sync(
            "GetCurrentState", None, Gio.DBusCallFlags.NONE, 5000, None
        )
        _serial, monitors, logical_monitors, _properties = reply.unpack()
        builtins = set()
        for monitor in monitors:
            spec, _modes, properties = monitor
            connector = spec[0]
            if properties.get("is-builtin", False):
                builtins.add(connector)
        for logical in logical_monitors:
            transform = logical[3]
            physical = logical[5]
            if any(monitor[0] in builtins for monitor in physical):
                return int(transform) & 3
        raise RuntimeError("Mutter reports no built-in display")

    def set_matrix(self, transform):
        route = tuple(self.config["routes"][str(transform * 90)])
        params = []
        names = self.config["mixer_nodes"]
        for name, source in zip(names, route):
            for channel in range(4):
                params.extend((f"{name}:Gain {channel + 1}",
                               1.0 if source == channel else 0.0))
        dump = subprocess.run(["pw-dump"], check=True, text=True,
                              stdout=subprocess.PIPE, timeout=5)
        nodes = json.loads(dump.stdout)
        target = self.config["control_node"]
        node = next(
            obj for obj in nodes
            if obj.get("type", "").endswith(":Node")
            and obj.get("info", {}).get("props", {}).get("node.name") == target
        )
        node_id = node["id"]
        # Global IDs can be reused in the same server after a node is removed.
        identity = (node_id, node["info"]["props"]["object.serial"])
        if self.last_node == identity and self.last_transform == transform:
            return
        subprocess.run(
            ["pw-cli", "set-param", str(node_id), "Props",
             json.dumps({"params": params}, separators=(",", ":"))],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, timeout=5,
        )
        self.last_transform = transform
        self.last_node = identity

    def update(self):
        try:
            transform = self.internal_transform()
            self.set_matrix(transform)
        except (GLib.Error, RuntimeError, StopIteration, KeyError, ValueError,
                subprocess.SubprocessError) as exc:
            print(f"speaker-rotation: {exc}", file=sys.stderr, flush=True)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: speaker-rotation.py CONFIG.json")
    with open(sys.argv[1], encoding="utf-8") as stream:
        config = json.load(stream)
    service = SpeakerRotation(config)
    service.update()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
