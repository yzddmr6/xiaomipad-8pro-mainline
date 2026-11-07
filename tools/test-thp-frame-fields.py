#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import importlib.util
import io
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('fields', Path(__file__).with_name('thp-frame-fields.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def record(sequence=1, frequency=7, flags=1, corrupt=False, data_type=3, size=64):
    p = bytearray(size)
    p[0] = 0xa5
    words = 11
    struct.pack_into('<I', p, 8, words)
    struct.pack_into('<I', p, 16, words ^ 0xffffffff)
    p[24] = 2
    struct.pack_into('<4H', p, 32, 257, 513, 1025, 2049)
    struct.pack_into('<H', p, 52, 240)
    p[54] = frequency
    p[56] = data_type
    if size >= 100:
        p[48], p[49] = 60, 40
        p[64 + 7], p[64 + 8] = 12, 40
        p[64 + 9], p[64 + 10] = 60, 8
        p[64 + 11] = 3
        p[64 + 17] = 0
    crc = -sum(struct.unpack_from('<22H', p, 20)) & 0xffff
    struct.pack_into('<H', p, 4, crc)
    struct.pack_into('<H', p, 12, crc ^ 0xffff)
    if corrupt:
        p[32] ^= 1
    return (m.HEADER.pack(0x3150544e, 32, 257 + len(p), sequence, sequence * 10000000, crc, flags,
                           words) + bytes(257) + p)


class Fields(unittest.TestCase):
    def test_payload_offsets_and_little_endian(self):
        row = list(m.changes(io.BytesIO(record())))[0]
        self.assertEqual(row['fields'], {'frequency_index': 7, 'noise': [257, 513, 1025, 2049],
                                         'scan_rate': 240, 'mode': 2, 'data_type': 3})
        self.assertEqual(row['boottime_ns'], 10000000)

    def test_pen_frame_fields(self):
        row = list(m.changes(io.BytesIO(record(data_type=29, size=200))))[0]
        self.assertEqual(row['fields']['data_type'], 29)
        self.assertEqual(row['fields']['pen'], {
            'quarter': 3, 'main_touch_columns': 60, 'main_touch_rows': 40,
            'stylus_columns_1': 12, 'stylus_rows_1': 40,
            'stylus_columns_2': 60, 'stylus_rows_2': 8,
            'frequency_request': 0})

    def test_a_finger_frame_has_no_pen_fields(self):
        row = list(m.changes(io.BytesIO(record(data_type=3, size=200))))[0]
        self.assertNotIn('pen', row['fields'])

    def test_changes_epoch_and_loss(self):
        rows = list(m.changes(io.BytesIO(record() + record(2) + record(3, 8) + record(4, 8, 3) + record(6, 8))))
        self.assertEqual([r['sequence'] for r in rows], [1, 3, 4, 6])
        self.assertTrue(rows[-1]['sequence_gap'])
        self.assertEqual(rows[-1]['elapsed_s'], .05)

    def test_invalid_never_becomes_metadata(self):
        uncovered = bytearray(record())
        start = m.HEADER.size + m.PREFIX
        for offset, fmt, value in [(24, '<H', 0), (28, '<I', 0),
                                   (start + 4, '<H', 0), (start + 8, '<I', 0),
                                   (start + 12, '<H', 0xffff), (start + 16, '<I', 0xffffffff)]:
            struct.pack_into(fmt, uncovered, offset, value)
        for data in [record(flags=0), record(corrupt=True), uncovered]:
            row = list(m.changes(io.BytesIO(data)))[0]
            self.assertFalse(row['valid'])
            self.assertNotIn('fields', row)

    def test_truncated_and_bad_header(self):
        for data in [record()[:-1], record()[:31], record()[:32], b'xxxx' + record()[4:]]:
            with self.assertRaises(ValueError):
                list(m.changes(io.BytesIO(data)))

    def test_timestamp_reversal(self):
        with self.assertRaisesRegex(ValueError, 'timestamp'):
            list(m.changes(io.BytesIO(record(2) + record(1))))


if __name__ == '__main__':
    unittest.main()
