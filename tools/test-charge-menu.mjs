// SPDX-License-Identifier: MIT
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const relative = ['device/charge-policy', 'packaging/shared-charge'].find(p => fs.existsSync(path.join(repo, p)));
const source = fs.readFileSync(path.join(repo, relative, 'usr/share/gnome-shell/extensions/charge-indicator@tablet-linux/extension.js'), 'utf8');
const marker = "        this._behaviour.connect('toggled', (_item, on) => {";
const start = source.indexOf(marker) + marker.length;
assert(start >= marker.length);
const body = source.slice(start, source.indexOf('\n        });', start));
let knob = 'auto [inhibit-charge]';
let writes = 0;
const Gio = {
    FileQueryInfoFlags: {NONE: 0}, SubprocessFlags: {NONE: 0},
    File: {new_for_path() { return {
        enumerate_children() {
            let returned = false;
            return {next_file() {if (returned) return null; returned = true; return {get_name: () => 'battery'};}, close() {}};
        },
        get_child() { return {get_child(name) {return {
            query_exists: () => knob !== null,
            load_contents: () => [true, Buffer.from(name === 'type' ? 'Battery' : knob)],
        };}};},
    };}},
    Subprocess: {new(args) {
        assert.deepEqual(args, ['pkexec', '/usr/libexec/tablet-charge-behaviour', 'auto']);
        writes++;
        knob = '[auto] inhibit-charge';
        return {wait_async(_, callback) {callback({wait_finish() {}}, {});}};
    }},
};
const toggle = Function('Gio', `return function(_item, on) {${body}}`)(Gio);
const first = source.indexOf('    _refresh() {') + '    _refresh() {'.length;
const refresh = Function('Gio', `return function() {${source.slice(first, source.indexOf('        this._stop();', first))}}`)(Gio);
const context = {_button: {}, _behaviour: {setToggleState(value) {toggle.call(context, null, value);}}};
context._refresh = refresh.bind(context);
context._refresh();
assert.equal(writes, 0, 'reading persisted state must not invoke pkexec');
assert.equal(context._behaviour.visible, true);
toggle.call(context, null, false);
assert.equal(writes, 1, 'one user toggle must cause exactly one write');
knob = null;
context._refresh();
assert.equal(context._behaviour.visible, false);
assert.equal(writes, 1);
console.log('PASS: state readback, one user write, absent attribute');
