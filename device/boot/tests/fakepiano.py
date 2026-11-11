# SPDX-License-Identifier: MIT
"""Synthetic piano root for the slot tools: sysfs, /dev files, GPTs and image sets.

Everything is generated: no device record, GUID, serial or image from the
test unit.  Disks are sparse files that hold only their GPT; partitions are
separate files sized like their GPT entries.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import uuid
import zlib

BOOT = Path(__file__).resolve().parents[1]
if str(BOOT) not in sys.path:
    sys.path.insert(0, str(BOOT))
import piano_bootslot as slot  # noqa: E402

LBS = 4096
DISK_LBAS = 1024
COUNT = 96
SERIAL = 'test0001'
ROOT_UUID = '11111111-2222-4333-8444-555555555555'
UBUNTU_CMDLINE = (f'root=PARTUUID={ROOT_UUID} rootfstype=ext4 rw rootwait console=ttyMSM0,115200n8 '
                  'log_buf_len=2M panic=15 msm.separate_gpu_kms=1 piano.boot=persistent '
                  f'systemd.gpt_auto=0 bootconfig androidboot.serialno={SERIAL}')
ABL_APPEND = ('androidboot.verifiedbootstate=orange androidboot.bootdevice=1d84000.ufshc '
              f'androidboot.serialno={SERIAL} console=ttynull')
ANDROID_VENDOR_CMDLINE = 'video=vfb:640x400,bpp=32,memsize=3072000 androidboot.hardware=qcom'
SIZES = {'boot': 8 * LBS, 'init_boot': 4 * LBS, 'vendor_boot': 8 * LBS}
LINUX_TYPE = '0fc63daf-8483-4772-8e79-3d69d8477de4'
# As observed on piano (mainline sysfs, live FDT ufs@1d84000, kmsg "ufshcd-qcom 1d84000.ufs").
UFS_DEVICE = 'soc@0/1d84000.ufs'
UFS_NODE = 'soc@0/ufs@1d84000'
UFS_COMPATIBLE = b'qcom,sm8750-ufshc\0qcom,ufshc\0jedec,ufs-2.0\0'
UFS_DRIVER = 'ufshcd-qcom'
USB_DEVICE = 'soc@0/a600000.usb/xhci-hcd.0.auto/usb1/1-1/1-1:1.0'
QC_TYPE = '20117f86-e985-4357-b9ee-374bc1d8487d'


def load_tool(name):
    path = BOOT / name
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def guid(seed):
    return str(uuid.UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16], version=4))


def crc(data):
    return zlib.crc32(data) & 0xffffffff


def filler(tag, size):
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f'{tag}:{counter}'.encode()).digest()
        counter += 1
    return bytes(out[:size])


def boot_image(size, cmdline, tag):
    data = bytearray(filler(tag, size))
    data[:4096] = bytes(4096)
    data[:8] = b'ANDROID!'
    struct.pack_into('<IIII', data, 8, 4096, 0, 0, 1584)
    struct.pack_into('<I', data, 40, 4)
    data[44:44 + len(cmdline)] = cmdline.encode()
    return bytes(data)


def vendor_image(size, cmdline, tag):
    data = bytearray(filler(tag, size))
    data[:4096] = bytes(4096)
    data[:8] = b'VNDRBOOT'
    struct.pack_into('<II', data, 8, 4, 4096)
    data[28:28 + len(cmdline)] = cmdline.encode()
    return bytes(data)


def image_set(kind, tag, cmdline=UBUNTU_CMDLINE):
    if kind == 'ubuntu':
        return {'boot': boot_image(SIZES['boot'], cmdline, tag + 'b'),
                'init_boot': boot_image(SIZES['init_boot'], '', tag + 'i'),
                'vendor_boot': vendor_image(SIZES['vendor_boot'], '', tag + 'v')}
    return {'boot': boot_image(SIZES['boot'], '', tag + 'b'),
            'init_boot': boot_image(SIZES['init_boot'], '', tag + 'i'),
            'vendor_boot': vendor_image(SIZES['vendor_boot'], ANDROID_VENDOR_CMDLINE, tag + 'v')}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Row:
    def __init__(self, name, first, blocks, attrs=0, type_guid=QC_TYPE):
        self.name, self.first, self.last, self.attrs = name, first, first + blocks - 1, attrs
        self.type_guid, self.unique_guid = type_guid, guid('part:' + name)


def attrs(byte):
    return byte << 48


class FakeDisk:
    def __init__(self, fp, name, lun, rows, layout='xiaomi', parent=None):
        parent = parent or fp.ufs_device
        self.fp, self.name, self.lun, self.rows, self.layout = fp, name, lun, rows, layout
        self.disk_guid = guid('disk:' + name)
        self.sys = fp.root / f'sys/devices/platform/{parent}/host0/target0:0:0/0:0:0:{lun}/block/{name}'
        self.sys.mkdir(parents=True)
        (self.sys / 'queue').mkdir()
        (self.sys / 'queue/logical_block_size').write_text(f'{LBS}\n')
        (self.sys / 'size').write_text(f'{DISK_LBAS * LBS // 512}\n')
        (self.sys / 'ro').write_text('0\n')
        link = fp.root / 'sys/block' / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(self.sys, link.parent))
        self.node = fp.root / 'dev' / name
        with open(self.node, 'wb') as f:
            f.truncate(DISK_LBAS * LBS)
        for number, row in enumerate(rows, 1):
            part = self.sys / f'{name}{number}'
            part.mkdir()
            (part / 'partition').write_text(f'{number}\n')
            (part / 'start').write_text(f'{row.first * LBS // 512}\n')
            (part / 'size').write_text(f'{(row.last - row.first + 1) * LBS // 512}\n')
            (part / 'dev').write_text(f'8:{lun * 16 + number}\n')
        self.write_gpt()

    def part_node(self, label):
        number = next(i for i, r in enumerate(self.rows, 1) if r.name == label)
        return self.fp.root / 'dev' / f'{self.name}{number}'

    def blobs(self):
        entries = bytearray(COUNT * 128)
        for i, row in enumerate(self.rows):
            e = bytearray(128)
            e[:16] = uuid.UUID(row.type_guid).bytes_le
            e[16:32] = uuid.UUID(row.unique_guid).bytes_le
            struct.pack_into('<QQQ', e, 32, row.first, row.last, row.attrs)
            name = row.name.encode('utf-16-le')
            e[56:56 + len(name)] = name
            entries[i * 128:(i + 1) * 128] = e
        back_entries = DISK_LBAS - (7 if self.layout == 'xiaomi' else 4)
        last_usable = DISK_LBAS - (8 if self.layout == 'xiaomi' else 5)

        def header(my, alt, entries_lba):
            h = bytearray(LBS)
            h[:8] = b'EFI PART'
            struct.pack_into('<IIII', h, 8, 0x00010000, 92, 0, 0)
            struct.pack_into('<QQQQ', h, 24, my, alt, 6, last_usable)
            h[56:72] = uuid.UUID(self.disk_guid).bytes_le
            struct.pack_into('<QIII', h, 72, entries_lba, COUNT, 128, 0)
            return h
        return {'primary_header': header(1, DISK_LBAS - 1, 2), 'primary_entries': entries,
                'backup_header': header(DISK_LBAS - 1, 1, back_entries), 'backup_entries': bytearray(entries)}

    @staticmethod
    def seal(blobs, which):
        h, e = blobs[which + '_header'], blobs[which + '_entries']
        struct.pack_into('<I', h, 88, crc(e))
        struct.pack_into('<I', h, 16, 0)
        struct.pack_into('<I', h, 16, crc(h[:92]))

    def write_gpt(self, edit=None, after_seal=None):
        """Write both copies; `edit` mutates blobs before sealing, `after_seal` after."""
        b = self.blobs()
        if edit:
            edit(b)
        self.seal(b, 'primary')
        self.seal(b, 'backup')
        if after_seal:
            after_seal(b)
        p_entries_lba = struct.unpack_from('<Q', b['primary_header'], 72)[0]
        b_entries_lba = struct.unpack_from('<Q', b['backup_header'], 72)[0]
        with open(self.node, 'r+b') as f:
            for lba, data in ((1, b['primary_header']), (2, b['primary_entries']),
                              (DISK_LBAS - 1, b['backup_header']), (b_entries_lba, b['backup_entries'])):
                f.seek(lba * LBS)
                f.write(data)
            assert p_entries_lba == 2

    def raw(self):
        return self.node.read_bytes()

    def attrs(self, label, which='primary'):
        data = self.raw()
        h = data[LBS:2 * LBS] if which == 'primary' else data[(DISK_LBAS - 1) * LBS:DISK_LBAS * LBS]
        lba = struct.unpack_from('<Q', h, 72)[0]
        i = next(i for i, r in enumerate(self.rows) if r.name == label)
        return struct.unpack_from('<Q', data, lba * LBS + i * 128 + 48)[0]


def platform_device(root, device, node, compatible, driver):
    """sysfs platform device with of_node and driver links, plus its bus entry."""
    dev = root / 'sys/devices/platform' / device
    dev.mkdir(parents=True, exist_ok=True)
    of = root / 'sys/firmware/devicetree/base' / node
    of.mkdir(parents=True, exist_ok=True)
    (of / 'compatible').write_bytes(compatible)
    (dev / 'of_node').symlink_to(os.path.relpath(of, dev))
    if driver:
        drv = root / 'sys/bus/platform/drivers' / driver
        drv.mkdir(parents=True, exist_ok=True)
        (dev / 'driver').symlink_to(os.path.relpath(drv, dev))
    bus = root / 'sys/bus/platform/devices' / dev.name
    bus.parent.mkdir(parents=True, exist_ok=True)
    bus.symlink_to(os.path.relpath(dev, bus.parent))
    return dev


class FakePiano:
    def __init__(self, root, boot_lun_name='sde', layout='xiaomi', occupant='ubuntu',
                 ufs=(UFS_DEVICE, UFS_NODE, UFS_COMPATIBLE, UFS_DRIVER)):
        self.root = Path(root)
        battery = self.root / 'sys/class/power_supply/battery'
        battery.mkdir(parents=True)
        (battery / 'type').write_text('Battery\n')
        (battery / 'capacity').write_text('80\n')
        for d in ('proc/self', 'run', 'dev', 'etc/piano', 'sys/firmware/devicetree/base', 'var/lib/piano'):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self.ufs_device = ufs[0]
        platform_device(self.root, *ufs)
        platform_device(self.root, 'soc@0/a600000.usb', 'soc@0/usb@a600000', b'qcom,sm8750-dwc3\0qcom,snps-dwc3\0',
                        'dwc3-qcom')
        (self.root / 'sys/firmware/devicetree/base/compatible').write_bytes(b'xiaomi,piano\0qcom,sm8750\0')
        (self.root / 'proc/uptime').write_text('100.00 400.00\n')
        (self.root / 'proc/self/mountinfo').write_text(
            '22 1 8:15 / / rw,relatime shared:1 - ext4 /dev/sda15 rw\n')
        self.cmdline(UBUNTU_CMDLINE + ' ' + ABL_APPEND)
        names = ['sda', 'sdb', 'sdc', 'sdd', 'sde', 'sdf']
        names.remove(boot_lun_name)
        self.lun0 = FakeDisk(self, names[0], 0, [
            Row('switch', 6, 2), Row('userdata', 8, 200),
            Row('linux_rootfs', 208, 100, type_guid=LINUX_TYPE), Row('linux_data', 308, 100, type_guid=LINUX_TYPE)])
        self.lun1 = FakeDisk(self, names[1], 1, [Row('xbl_a', 6, 16, attrs(0x44))])
        start = 6
        rows = []
        for label, blocks, a in (('abl_a', 4, 0x44), ('boot_a', 8, 0x37), ('vendor_boot_a', 8, 0x44),
                                 ('init_boot_a', 4, 0x44), ('vbmeta_a', 2, 0x44),
                                 ('abl_b', 4, 0), ('boot_b', 8, 0x02), ('vendor_boot_b', 8, 0),
                                 ('init_boot_b', 4, 0)):
            rows.append(Row(label, start, blocks, attrs(a)))
            start += blocks
        self.lun4 = FakeDisk(self, boot_lun_name, 4, rows, layout=layout)
        self.sets = {'ubuntu': image_set('ubuntu', 'u1'), 'ubuntu.prev': image_set('ubuntu', 'u0'),
                     'android': image_set('android', 'a0')}
        for name, images in self.sets.items():
            self.store_set(name, images)
        self.fill_slot(self.sets[occupant])
        parts = []
        for n in slot.NAMES:
            row = next(r for r in rows if r.name == n + '_a')
            parts.append({'label': n + '_a', 'partuuid': row.unique_guid, 'lun': 4,
                          'start_bytes': row.first * LBS, 'bytes': (row.last - row.first + 1) * LBS,
                          'disk_guid': self.lun4.disk_guid})
        (self.root / 'etc/piano/boot-partitions.json').write_text(json.dumps(parts))
        (self.root / 'etc/piano/boot-profile.json').write_text(json.dumps({'serial': SERIAL}))
        (self.root / 'etc/piano/layout.json').write_text(json.dumps({'root_partuuid': ROOT_UUID}))
        self.host = slot.Host(self.root)

    def cmdline(self, text):
        (self.root / 'proc/cmdline').write_text(text + '\n')

    def store_set(self, name, images, sums=True):
        return self.image_dir(self.root / 'var/lib/piano/switch' / name, images, sums)

    def image_dir(self, d, images, sums=True):
        d = self.root / d
        d.mkdir(parents=True, exist_ok=True)
        for n, data in images.items():
            (d / (n + '.img')).write_bytes(data)
        if sums:
            (d / 'SHA256SUMS').write_text(''.join(f'{sha(images[n])}  {n}.img\n' for n in sorted(images)))
        return d

    def fill_slot(self, images):
        for n, data in images.items():
            self.lun4.part_node(n + '_a').write_bytes(data)
        for n in slot.NAMES:  # B-slot partitions exist as files too
            node = self.lun4.part_node(n + '_b')
            if not node.exists():
                node.write_bytes(bytes(SIZES[n]))

    def slot_content(self):
        return {n: self.lun4.part_node(n + '_a').read_bytes() for n in slot.NAMES}

    def set_boot_attrs(self, a=None, b=None):
        for row in self.lun4.rows:
            if row.name == 'boot_a' and a is not None:
                row.attrs = attrs(a)
            if row.name == 'boot_b' and b is not None:
                row.attrs = attrs(b)
        self.lun4.write_gpt()

    def all_disks(self):
        return {d.name: d.raw() for d in (self.lun0, self.lun1, self.lun4)}
