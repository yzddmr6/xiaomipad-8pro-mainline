// SPDX-License-Identifier: MIT
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {exitReason} from './reasons.js';

const dash = '—';
const chinese = /^zh([_.@-]|$)/i.test(GLib.get_language_names()[0]);
const tr = (en, zh) => chinese ? zh : en;
function format(value, unit, digits = 0, minimum = digits) {
    if (!Number.isFinite(value))
        return dash;
    const number = value.toLocaleString(undefined, {
        minimumFractionDigits: minimum,
        maximumFractionDigits: digits,
    });
    return `${number} ${unit}`;
}

function available(...parts) {
    return parts.filter(part => part !== dash).join(' · ') || dash;
}

function timeToFull(minutes) {
    const unit = tr('min', '分钟');
    if (!Number.isFinite(minutes) || minutes <= 60)
        return format(minutes, unit);
    return `${format(Math.floor(minutes / 60), tr('h', '小时'))} ${format(minutes % 60, unit)}`;
}

export default class ChargeIndicator extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._button = new PanelMenu.Button(0.5, tr('Charging power', '充电功率'));
        this._label = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        this._button.add_child(this._label);
        this._rows = {};
        const row = (key, title) => {
            const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
            item.add_child(new St.Label({text: title, x_expand: true}));
            this._rows[key] = new St.Label({text: dash});
            item.add_child(this._rows[key]);
            this._button.menu.addMenuItem(item);
            return item;
        };
        row('input', tr('Charger input', '充电器输入'));
        row('battery', tr('Net battery power', '电池净功率'));
        row('cells', tr('Cell voltage', '电芯电压'));
        row('temperature', tr('Battery temperature', '电池温度'));
        row('eta', tr('Time to full at this rate', '按当前功率充满'));
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        row('path', tr('Charging path', '充电通路'));
        row('request', tr('Requested', '请求电压和电流'));
        row('exits', tr('Exits this connection', '本次快充退出'));
        row('reason', tr('Last exit', '上次退出原因'));
        this._awakeRow = row('awake', tr('Staying awake to keep charging', '保持唤醒以继续快充'));
        this._awakeRow.hide();
        this._rows.reason.clutter_text.line_wrap = true;
        this._rows.reason.style = 'max-width: 22em;';
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const toggle = new PopupMenu.PopupSwitchMenuItem(tr('Show on battery', '拔线后显示放电功率'),
            this._settings.get_boolean('show-on-battery'));
        this._button.menu.addMenuItem(toggle);
        toggle.connect('toggled', (_item, on) => {
            this._settings.set_boolean('show-on-battery', on);
            this._refresh();
        });
        Main.panel.addToStatusArea(this.uuid, this._button);
        this._button.hide();
        this._baseline = null;
        this._previousExits = null;
        this._partner = null;
        this._secondary = null;
        this._lastReason = null;
        this._screenOff = false;
        this._shield = Main.screenShield?.connect('active-changed', () => this._refresh());
        this._displaySignal = Gio.DBus.session.signal_subscribe('org.gnome.Mutter.DisplayConfig',
            'org.freedesktop.DBus.Properties', 'PropertiesChanged', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', Gio.DBusSignalFlags.NONE,
            (_bus, _sender, _path, _iface, _name, params) => {
                const power = params.deepUnpack()[1].PowerSaveMode;
                if (power) {
                    this._screenOff = power.get_int32() > 0;
                    this._refresh();
                }
            });
        this._powerSignal = Gio.DBus.system.signal_subscribe('org.freedesktop.UPower',
            'org.freedesktop.DBus.Properties', 'PropertiesChanged', '/org/freedesktop/UPower',
            'org.freedesktop.UPower', Gio.DBusSignalFlags.NONE,
            (_bus, _sender, _path, _iface, _name, params) => {
                if ('OnBattery' in params.deepUnpack()[1])
                    this._refresh();
            });
        this._refresh();
    }

    _stop() {
        if (this._timer)
            GLib.Source.remove(this._timer);
        this._timer = 0;
        if (this._process) {
            this._process.force_exit();
            this._process = null;
        }
    }

    _refresh() {
        this._stop();
        if (this._screenOff || Main.screenShield?.active)
            return;
        const args = ['/usr/libexec/tablet-charge-display'];
        if (this._settings.get_boolean('show-on-battery'))
            args.push('--on-battery');
        let proc;
        try {
            proc = Gio.Subprocess.new(args, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        } catch {
            this._button.hide();
            return;
        }
        this._process = proc;
        proc.communicate_utf8_async(null, null, (process, result) => {
            let sample = {};
            try {
                const [, output] = process.communicate_utf8_finish(result);
                if (process.get_successful())
                    sample = JSON.parse(output);
            } catch {
                // A disabled extension or a disappearing power supply has no sample.
            }
            if (this._process !== proc)
                return;
            this._process = null;
            this._update(sample);
            this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, sample.online ? 2000 : 30000, () => {
                this._timer = 0;
                this._refresh();
                return GLib.SOURCE_REMOVE;
            });
        });
    }

    _exitHistory(s) {
        let count = s.exits;
        const newPartner = this._partner && s.partner && this._partner !== s.partner;
        if (!s.online || newPartner) {
            this._lastReason = null;
            this._previousExits = null;
        }
        this._partner = s.partner;
        if (newPartner)
            this._baseline = null;
        // The serial retry counter resets on detach. PPS exits use a separate,
        // cumulative counter. Keep its baseline across a serial session.
        if (s.serial)
            return [s.serial.rehandshakes, s.serial.reason];
        // The fixed-voltage path exports its last reason without a counter.
        // An observed change can still explain why the voltage dropped.
        if (s.online && s.secondary && s.secondary !== 'none' &&
            (s.device === 'piano' || this._secondary != null) && s.secondary !== this._secondary)
            this._lastReason = s.secondary;
        this._secondary = s.secondary;
        if (s.device === 'erhai') {
            if (!s.online)
                this._baseline = Number.isFinite(count) ? count : null;
            if (count === 0)
                this._baseline = 0;
            count = Number.isFinite(count) && this._baseline !== null && count >= this._baseline
                ? count - this._baseline : null;
        }
        if (s.online && count > 0 && count !== this._previousExits && s.reason && s.reason !== 'none')
            this._lastReason = s.reason;
        this._previousExits = count;
        return [count, s.reason || s.secondary ? this._lastReason : null];
    }

    _update(s) {
        const [count, reason] = this._exitHistory(s);
        if (s.online == null || (!s.online && !this._settings.get_boolean('show-on-battery'))) {
            this._button.hide();
            return;
        }
        const net = format(s.watts, 'W', 1, 0);
        const path = s.online ? s.path ?? dash : tr('Battery', '电池供电');
        const down = s.watts < 0;
        this._label.text = `${down ? '↘' : '⚡'} ${available(net, path)}`;
        this._label.style = down ? 'color: #ffbe6f;' : null;
        this._button.accessible_name = this._label.text;
        const rows = this._rows;
        const inputPower = s.volts != null && s.amps != null ? s.volts * s.amps : null;
        rows.input.text = available(format(s.volts, 'V', 1), format(s.amps, 'A', 2), format(inputPower, 'W'));
        rows.battery.text = net;
        rows.cells.text = s.cells.map(c => format(c.volts, 'V', 2)).join(' / ');
        rows.temperature.text = s.cells.map(c => format(c.temp, '°C', 1)).join(' / ');
        rows.eta.text = timeToFull(s.minutes);
        rows.path.text = path;
        rows.request.text = available(format(s.request_v, 'V', 2, 0), format(s.request_a, 'A', 2, 0));
        rows.exits.text = s.online && Number.isFinite(count) ? String(count) : dash;
        rows.reason.text = s.online && reason && reason !== 'none' ? exitReason(reason, chinese) : dash;
        this._awakeRow.visible = Object.hasOwn(s, 'awake');
        rows.awake.text = s.awake == null ? dash : s.awake ? tr('Yes', '是') : tr('No', '否');
        this._button.show();
    }

    disable() {
        this._stop();
        if (this._shield)
            Main.screenShield.disconnect(this._shield);
        Gio.DBus.session.signal_unsubscribe(this._displaySignal);
        Gio.DBus.system.signal_unsubscribe(this._powerSignal);
        this._button.destroy();
        this._button = this._label = this._awakeRow = this._rows = this._settings = null;
    }
}
