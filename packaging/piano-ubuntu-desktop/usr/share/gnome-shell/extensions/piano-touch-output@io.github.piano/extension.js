// SPDX-License-Identifier: GPL-2.0-or-later
//
// Mutter maps a touchscreen to the monitor whose EDID vendor appears in the
// device name or whose physical size matches the device; with neither, a
// touchscreen goes to the built-in panel (meta-input-mapper.c,
// guess_candidates()). The touch layer of a USB-C portable monitor usually
// matches neither, so its touches land on the tablet's own panel. Android
// maps external touchscreens to the external display instead
// (TouchInputMapper, associatedDisplayIsExternal).
//
// The tablet has one external display output. While exactly one monitor
// other than the built-in panel is connected, every external (USB or
// Bluetooth) touchscreen gets it as its configured output, which Mutter
// applies at once; otherwise the setting is reset and Mutter's own matching
// applies again.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const EXTERNAL_BUSES = new Set([0x03 /* BUS_USB */, 0x05 /* BUS_BLUETOOTH */]);

function isExternalTouchscreen(device) {
    if (device.get_device_type() !== Clutter.InputDeviceType.TOUCHSCREEN_DEVICE)
        return false;
    const node = device.get_device_node();
    if (!node)
        return false;
    try {
        const [, bytes] = GLib.file_get_contents(
            `/sys/class/input/${GLib.path_get_basename(node)}/device/id/bustype`);
        return EXTERNAL_BUSES.has(parseInt(new TextDecoder().decode(bytes), 16));
    } catch {
        return false;
    }
}

function outputSettings(device) {
    const id = v => v.toString(16).padStart(4, '0');
    return new Gio.Settings({
        schema_id: 'org.gnome.desktop.peripherals.touchscreen',
        path: `/org/gnome/desktop/peripherals/touchscreens/${id(device.get_vendor_id())}:${id(device.get_product_id())}/`,
    });
}

export default class TouchOutputExtension extends Extension {
    enable() {
        this._seat = global.stage.context.get_backend().get_default_seat();
        this._monitorManager = global.backend.get_monitor_manager();
        this._external = null;
        this._query = 0;
        this._cancellable = new Gio.Cancellable();
        this._deviceAddedId = this._seat.connect('device-added', (_seat, device) => this._apply(device));
        this._monitorsChangedId = this._monitorManager.connect('monitors-changed', () => this._refresh());
        this._refresh();
    }

    disable() {
        this._seat.disconnect(this._deviceAddedId);
        this._monitorManager.disconnect(this._monitorsChangedId);
        this._cancellable.cancel();
        this._external = null;
        for (const device of this._seat.list_devices())
            this._apply(device);
        this._seat = this._monitorManager = this._cancellable = null;
    }

    _refresh() {
        // DisplayConfig is served by this process: call it asynchronously.
        const query = ++this._query;
        Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', null, null,
            Gio.DBusCallFlags.NONE, -1, this._cancellable, (connection, result) => {
                let monitors;
                try {
                    [, monitors] = connection.call_finish(result).recursiveUnpack();
                } catch (e) {
                    if (!e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        logError(e, 'piano-touch-output: GetCurrentState');
                    return;
                }
                if (query !== this._query)
                    return;
                const external = monitors.filter(([, , props]) => !props['is-builtin']);
                this._external = external.length === 1 ? external[0][0] : null;
                for (const device of this._seat.list_devices())
                    this._apply(device);
            });
    }

    _apply(device) {
        if (!isExternalTouchscreen(device))
            return;
        const settings = outputSettings(device);
        if (this._external) {
            const [connector, vendor, product, serial] = this._external;
            const output = [vendor, product, serial, connector];
            if (settings.get_strv('output').join('\n') !== output.join('\n')) {
                settings.set_strv('output', output);
                console.log(`piano-touch-output: ${device.get_device_name()} -> ${connector}`);
            }
        } else if (settings.get_user_value('output') !== null) {
            settings.reset('output');
            console.log(`piano-touch-output: ${device.get_device_name()} -> automatic`);
        }
    }
}
