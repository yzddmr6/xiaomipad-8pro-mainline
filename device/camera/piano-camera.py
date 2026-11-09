#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Small photo UI for camerad; previews stay on its standard V4L2 outputs."""
import array
import datetime
import os
from pathlib import Path
import signal
import socket
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gst, Gtk
import numpy as np

Gst.init(None)


class Camera(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.github.yzddmr6.PianoCamera')
        self.pipeline = self.pending = None
        self.closed = False
        self.preview_pending = False
        self.lock = threading.Lock()
        self.devices = []
        for index, label, title in [(0, 'Rear Camera', '后置'), (1, 'Front Camera', '前置')]:
            for node in sorted(Path('/sys/class/video4linux').glob('video*')):
                if (node / 'name').read_text().strip() == label:
                    self.devices.append((index, '/dev/' + node.name, title))
        self.connect('activate', self.activate)
        self.connect('shutdown', lambda *_: self.stop())
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.quit)

    def activate(self, *_):
        if self.get_active_window():
            self.get_active_window().present()
            return
        win = Gtk.ApplicationWindow(application=self, title='补光相机')
        win.set_default_size(900, 700)
        win.connect('close-request', lambda *_: self.stop() or False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.picture = Gtk.Picture(hexpand=True, vexpand=True, can_shrink=True)
        box.append(self.picture)
        bar = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.selector = Gtk.DropDown.new_from_strings([x[2] for x in self.devices])
        self.selector.connect('notify::selected', lambda *_: self.start_preview())
        bar.append(self.selector)
        self.light = Gtk.CheckButton(label='拍照补光')
        bar.append(self.light)
        self.shutter = Gtk.Button(label='拍照')
        self.shutter.connect('clicked', self.take_photo)
        bar.append(self.shutter)
        box.append(bar)
        self.status = Gtk.Label(label='照片保存到“图片/相机”', wrap=True)
        box.append(self.status)
        win.set_child(box)
        win.present()
        self.start_preview()

    def start_preview(self):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if not self.devices:
            self.status.set_label('未找到相机')
            self.shutter.set_sensitive(False)
            return
        index, path, _ = self.devices[self.selector.get_selected()]
        self.light.set_sensitive(index == 0)
        if index:
            self.light.set_active(False)
        self.shutter.set_sensitive(False)
        try:
            self.pipeline = Gst.parse_launch(
                f'v4l2src device={path} ! videoconvert ! videoscale ! '
                'video/x-raw,format=RGB,width=768,height=576 ! '
                'appsink name=preview emit-signals=true max-buffers=1 drop=true sync=false')
            self.pipeline.get_by_name('preview').connect('new-sample', self.sample)
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect('message::error', self.preview_error)
            if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('无法启动预览')
        except Exception as exc:
            self.status.set_label(str(exc))

    def preview_error(self, _bus, message):
        error, _debug = message.parse_error()
        self.status.set_label(str(error))
        self.shutter.set_sensitive(False)
        with self.lock:
            if self.pending:
                try:
                    self.pending.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.pending.close()
                self.pending = None

    def sample(self, sink):
        sample = sink.emit('pull-sample')
        if not sample or self.closed:
            return Gst.FlowReturn.OK
        with self.lock:
            if self.preview_pending:
                return Gst.FlowReturn.OK
            self.preview_pending = True
        buffer = sample.get_buffer()
        pixels = buffer.extract_dup(0, buffer.get_size())
        GLib.idle_add(self.show_preview, pixels)
        return Gst.FlowReturn.OK

    def show_preview(self, pixels):
        with self.lock:
            self.preview_pending = False
            busy = self.pending is not None
        if not self.closed:
            self.picture.set_paintable(Gdk.MemoryTexture.new(
                768, 576, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(pixels), 768 * 3))
            self.shutter.set_sensitive(not busy)
        return GLib.SOURCE_REMOVE

    def take_photo(self, *_):
        index, path, _ = self.devices[self.selector.get_selected()]
        illuminated = self.light.get_active() and index == 0
        self.shutter.set_sensitive(False)
        self.selector.set_sensitive(False)
        self.light.set_sensitive(False)
        self.status.set_label('正在测光并拍照…')
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        connection.settimeout(7)
        with self.lock:
            if self.pending:
                connection.close()
                return
            self.pending = connection
        threading.Thread(target=self.capture, args=(connection, index, path, illuminated),
                         daemon=True).start()

    def capture(self, connection, index, path, illuminated):
        fds = []
        try:
            connection.connect('/run/piano-camera/photo.sock')
            proof = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                connection.sendmsg([b'PC01' + bytes([index, int(illuminated), 0, 0])],
                                   [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [proof]))])
            finally:
                os.close(proof)
            data, ancillary, flags, _ = connection.recvmsg(256, socket.CMSG_SPACE(4))
            for level, kind, body in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    values = array.array('i')
                    values.frombytes(body[:len(body) - len(body) % values.itemsize])
                    fds.extend(values)
            fields = data.decode().split()
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(fds) != 1 or len(fields) != 5 or fields[0] != 'OK':
                raise RuntimeError(data.decode(errors='replace') or '拍照已取消')
            width, height, _sequence, lit = map(int, fields[1:])
            if not (0 < width <= 4096 and 0 < height <= 4096) or lit != int(illuminated):
                raise RuntimeError('相机返回了不匹配的照片')
            size = width * height * 4
            if os.fstat(fds[0]).st_size != size:
                raise RuntimeError('照片数据不完整')
            import mmap
            with mmap.mmap(fds[0], size, access=mmap.ACCESS_READ) as mapped:
                rgb = np.frombuffer(mapped, dtype=np.uint8).reshape(height, width, 4)[:, :, 2::-1].tobytes()
            if self.closed:
                return
            directory = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
                             or Path.home() / 'Pictures') / '相机'
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.jpg')
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgb), GdkPixbuf.Colorspace.RGB,
                                                   False, 8, width, height, width * 3)
            pixbuf.savev(str(target), 'jpeg', ['quality'], ['95'])
            message = '已保存：' + target.name
        except Exception as exc:
            message = '无法拍照：' + str(exc)
        finally:
            for fd in fds:
                os.close(fd)
            connection.close()
            with self.lock:
                if self.pending is connection:
                    self.pending = None
        GLib.idle_add(self.finished, message)

    def finished(self, message):
        if not self.closed:
            self.status.set_label(message)
            self.selector.set_sensitive(True)
            self.light.set_sensitive(self.devices[self.selector.get_selected()][0] == 0)
        return GLib.SOURCE_REMOVE

    def stop(self):
        self.closed = True
        with self.lock:
            if self.pending:
                try:
                    self.pending.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.pending.close()
                self.pending = None
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None


if __name__ == '__main__':
    Camera().run()
