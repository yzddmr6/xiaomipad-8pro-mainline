#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic, host-only tests. No real device records, block devices or commands."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import uuid
import zlib

TOOLS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('stock_restore', TOOLS / 'prepare-stock-restore.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
BS, BLOCKS = 4096, 4096
GUID = '11111111-2222-4333-8444-555555555555'


def entry(number, name, start, end, kind=None, attrs=0):
    result = bytearray(128)
    result[:16] = kind or uuid.UUID('11111111-1111-4111-8111-111111111111').bytes_le
    result[16:32] = uuid.UUID(int=number + 100).bytes_le
    struct.pack_into('<QQQ', result, 32, start, end, attrs)
    result[56:56 + len(name.encode('utf-16-le'))] = name.encode('utf-16-le')
    return bytes(result)


def fixture(current=False, change=None, guid=GUID, saturated=True, backup_change=None):
    entries = [bytes(128) for _ in range(64)]
    entries[0] = entry(1, 'keep', 6, 255)
    entries[33] = entry(34, 'userdata', 256, 2047 if current else 4090)
    if current:
        entries[34] = entry(35, 'linux_rootfs', 2048, 3071, M.LINUX_FS)
        entries[35] = entry(36, 'linux_data', 3072, 4090, M.LINUX_FS)
    if change:
        change(entries)
    array = b''.join(entries)
    backup_array = array
    if backup_change:
        temp = list(entries)
        backup_change(temp)
        backup_array = b''.join(temp)
    head, tail = bytearray(BS * 34), bytearray(BS * 34)
    head[510:512] = b'\x55\xaa'
    head[450] = 0xee
    struct.pack_into('<II', head, 454, 1, 0xffffffff if saturated else BLOCKS - 1)
    head[BS * 6:BS * 7] = b'S' * BS  # real partition data: must not be exported
    tail[:BS * 29] = b'D' * (BS * 29)  # userdata: must not be exported
    for target, offset, own, other, array_lba, arr in [
        (head, BS, 1, BLOCKS - 1, 2, array),
        (tail, BS * 33, BLOCKS - 1, 1, BLOCKS - 5, backup_array),
    ]:
        h = bytearray(BS)
        h[:8] = b'EFI PART'
        struct.pack_into('<IIIIQQQQ', h, 8, 0x10000, 92, 0, 0, own, other, 6, BLOCKS - 6)
        h[56:72] = uuid.UUID(guid).bytes_le
        struct.pack_into('<QIII', h, 72, array_lba, 64, 128, zlib.crc32(arr) & 0xffffffff)
        struct.pack_into('<I', h, 16, zlib.crc32(h[:92]) & 0xffffffff)
        target[offset:offset + BS] = h
        arr_offset = array_lba * BS if own == 1 else 29 * BS
        target[arr_offset:arr_offset + len(arr)] = arr
    return bytes(head), bytes(tail)


def parsed(current=False, **kwargs):
    return M.parse_pair(*fixture(current, **kwargs), BS, BLOCKS, GUID)


class StockRestore(unittest.TestCase):
    def test_expected_layout_and_exact_reserved_regions(self):
        stock, current = parsed(), parsed(True, saturated=False)
        layout = M.compare_layout(stock, current)
        self.assertEqual(layout['removed_linux_entries'], [35, 36])
        self.assertEqual(len(stock['head']), BS * 6)
        self.assertEqual(len(stock['tail']), BS * 5)
        self.assertEqual(stock['tail_start'], BLOCKS - 5)
        self.assertNotIn(b'S' * BS, stock['head'])
        self.assertNotIn(b'D' * BS, stock['tail'])

    def test_full_and_exact_region_inputs_agree(self):
        stock = parsed()
        smaller = M.parse_pair(stock['head'], stock['tail'], BS, BLOCKS, GUID)
        self.assertEqual(stock['header']['array'], smaller['header']['array'])

    def test_header_crc_damage(self):
        head, tail = fixture()
        broken = bytearray(head)
        broken[BS + 16] ^= 1
        with self.assertRaisesRegex(ValueError, 'header CRC'):
            M.parse_pair(bytes(broken), tail, BS, BLOCKS, GUID)

    def test_array_crc_damage(self):
        head, tail = fixture()
        broken = bytearray(head)
        broken[BS * 2 + 16] ^= 1
        with self.assertRaisesRegex(ValueError, 'entry-array CRC'):
            M.parse_pair(bytes(broken), tail, BS, BLOCKS, GUID)

    def test_valid_but_different_primary_backup_arrays(self):
        with self.assertRaisesRegex(ValueError, 'primary/backup mismatch'):
            parsed(backup_change=lambda e: e.__setitem__(0, entry(1, 'other', 6, 255)))

    def test_wrong_guid(self):
        with self.assertRaisesRegex(ValueError, 'disk GUID'):
            M.parse_pair(*fixture(), BS, BLOCKS, '22222222-2222-4222-8222-222222222222')

    def test_wrong_geometry(self):
        with self.assertRaisesRegex(ValueError, 'LBA pair'):
            M.parse_pair(*fixture(), BS, BLOCKS + 1, GUID)

    def test_unobserved_sector_size_refused(self):
        with self.assertRaisesRegex(ValueError, '4096'):
            M.parse_pair(*fixture(), 512, BLOCKS, GUID)

    def test_original_partition_change(self):
        current = parsed(True, change=lambda e: e.__setitem__(0, entry(1, 'keep', 6, 255, attrs=1)))
        with self.assertRaisesRegex(ValueError, 'non-userdata'):
            M.compare_layout(parsed(), current)

    def test_original_partition_guid_change(self):
        def change(e):
            raw = bytearray(e[0]); raw[16:32] = uuid.UUID(int=999).bytes_le; e[0] = bytes(raw)
        with self.assertRaisesRegex(ValueError, 'non-userdata'):
            M.compare_layout(parsed(), parsed(True, change=change))

    def test_userdata_start_change(self):
        with self.assertRaisesRegex(ValueError, 'beyond shrinking'):
            M.compare_layout(parsed(), parsed(True, change=lambda e: e.__setitem__(33, entry(34, 'userdata', 257, 2047))))

    def test_overlapping_partition(self):
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            parsed(True, change=lambda e: e.__setitem__(34, entry(35, 'linux_rootfs', 2047, 3071, M.LINUX_FS)))

    def test_partition_outside_usable_storage(self):
        with self.assertRaisesRegex(ValueError, 'outside usable'):
            parsed(True, change=lambda e: e.__setitem__(35, entry(36, 'linux_data', 3072, 4091, M.LINUX_FS)))

    def test_unrecognized_added_partition(self):
        def change(e):
            e[35] = entry(36, 'linux_data', 3072, 4089, M.LINUX_FS)
            e[36] = entry(37, 'unknown', 4090, 4090)
        with self.assertRaisesRegex(ValueError, 'unexpected added'):
            M.compare_layout(parsed(), parsed(True, change=change))

    def test_unknown_linux_name(self):
        current = parsed(True, change=lambda e: e.__setitem__(35, entry(36, 'other', 3072, 4090, M.LINUX_FS)))
        with self.assertRaisesRegex(ValueError, 'unexpected added'):
            M.compare_layout(parsed(), current)

    def test_gap_in_linux_replacement(self):
        current = parsed(True, change=lambda e: e.__setitem__(34, entry(35, 'linux_rootfs', 2049, 3071, M.LINUX_FS)))
        with self.assertRaisesRegex(ValueError, 'exactly fill'):
            M.compare_layout(parsed(), current)

    def test_duplicate_partition_guid(self):
        def change(e):
            raw = bytearray(e[35]); raw[16:32] = e[34][16:32]; e[35] = bytes(raw)
        with self.assertRaisesRegex(ValueError, 'duplicate partition GUID'):
            parsed(True, change=change)

    def test_sparse_unused_entry_is_not_silently_ignored(self):
        def change(e):
            raw = bytearray(128); raw[-1] = 1; e[-1] = bytes(raw)
        with self.assertRaisesRegex(ValueError, 'unused entry'):
            parsed(change=change)

    def test_saturated_and_actual_pmbr_both_accepted(self):
        for saturated in (True, False):
            self.assertEqual(parsed(saturated=saturated)['head_blocks'], 6)

    def test_unprefixed_fastboot_hex(self):
        for text in ('73517FB000', '0x73517fb000'):
            self.assertEqual(M.fastboot_size(text), 495288561664)
        with self.assertRaises(ValueError):
            M.fastboot_size('73517FB000 trailing')

    def test_zero_returncode_with_failed_output_is_failure(self):
        self.assertFalse(M.fastboot_succeeded(0, "FAILED (remote: 'failure')"))
        self.assertFalse(M.fastboot_succeeded(1, 'OKAY'))
        self.assertTrue(M.fastboot_succeeded(0, 'Writing super OKAY\nFinished.'))

    def test_changed_factory_script_refused(self):
        with self.assertRaisesRegex(ValueError, 'audited'):
            M.factory_actions(b'fastboot $* oem lock\n')

    def test_regular_only_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'data').write_bytes(b'test'); (root / 'link').symlink_to(root / 'data')
            self.assertEqual(M.read_regular(root / 'data'), b'test')
            with self.assertRaises((OSError, ValueError)): M.read_regular(root / 'link')
            with self.assertRaises(ValueError): M.read_regular(root)

    def test_nonregular_input_is_rejected_before_open(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import stat
        with patch.object(M.os, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFBLK)), patch.object(M.os, 'open') as opened:
            with self.assertRaisesRegex(ValueError, 'regular file'):
                M.read_regular('/synthetic/not-a-real-device')
            opened.assert_not_called()

    def test_changed_open_identity_is_refused(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'backup'
            other = Path(d) / 'other'
            path.write_bytes(b'backup'); other.write_bytes(b'backup')
            with patch.object(M.os, 'fstat', return_value=other.stat()):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    M.read_regular(path)

    def test_default_validation_and_explicit_output_are_host_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, data in zip(('sh', 'st', 'ch', 'ct'), fixture() + fixture(True)):
                (root / name).write_bytes(data)
            args = argparse.Namespace(stock_head=root / 'sh', stock_tail=root / 'st', current_head=root / 'ch',
                                      current_tail=root / 'ct', block_size=BS, disk_blocks=BLOCKS,
                                      expected_disk_guid=GUID, factory_script=None, output=None)
            before = set(root.iterdir())
            result = M.prepare(args)
            self.assertEqual(set(root.iterdir()), before)
            self.assertEqual(result['state'], 'VALIDATED_OFFLINE_NOT_DEVICE_AUTHORIZATION')
            args.output = root / 'output'
            M.prepare(args)
            self.assertEqual((args.output / 'stock-primary.bin').stat().st_size, 24576)
            self.assertEqual((args.output / 'stock-backup.bin').stat().st_size, 20480)
            self.assertEqual(len(list(args.output.iterdir())), 6)
            with self.assertRaises(FileExistsError): M.prepare(args)


if __name__ == '__main__':
    unittest.main()
