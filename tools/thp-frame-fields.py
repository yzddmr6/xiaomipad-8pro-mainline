#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read NTP1 --record files and emit changes in vendor frame metadata as JSONL.

Offsets are relative to the THP payload, after the 32-byte NTP1 header and
257-byte point prefix. Field definitions follow MiCode's nt36xxx.c at commit
6957f6b646d1c919e175e6f9000eb50c8635273c, nvt frame parser (around line 2819).
No input events, calibration, commands or live device opens are involved.
"""
import argparse
import json
from pathlib import Path
import stat
import struct
import sys
import time

HEADER = struct.Struct('<IHHQQHHI')
PREFIX = 257
MAX_FRAME = 8191


def decode(header, frame):
    magic, size, length, sequence, stamp, crc, flags, words = HEADER.unpack(header)
    if magic != 0x3150544e or size != HEADER.size or not PREFIX + 64 <= length <= MAX_FRAME or flags & ~3:
        raise ValueError('invalid NTP1 header')
    if len(frame) != length or frame[0] != 0:
        raise ValueError('invalid NTP1 frame size/prefix')
    p = frame[PREFIX:]
    row = {'sequence': sequence, 'boottime_ns': stamp, 'epoch': bool(flags & 2)}
    nwords = struct.unpack_from('<I', p, 8)[0]
    pcrc = struct.unpack_from('<H', p, 4)[0]
    valid = (bool(flags & 1) and p[0] == 0xa5 and words == nwords and crc == pcrc and
             9 <= nwords <= (len(p) - 20) // 4 and
             struct.unpack_from('<I', p, 16)[0] == nwords ^ 0xffffffff and
             struct.unpack_from('<H', p, 12)[0] == pcrc ^ 0xffff)
    if valid:
        valid = (sum(struct.unpack_from('<' + 'H' * (nwords * 2), p, 20)) + pcrc) & 0xffff == 0
    row['valid'] = valid
    if valid:
        row['fields'] = {'frequency_index': p[54],
                         'noise': list(struct.unpack_from('<4H', p, 32)),
                         'scan_rate': struct.unpack_from('<H', p, 52)[0],
                         'mode': p[24],
                         'data_type': p[56]}
        if p[56] == 29 and len(p) >= 100:
            # The pen frame carries the main touch as four quarters and the
            # two stylus planes of 12 by 40 and 60 by 8.
            request = p[64 + 17]
            row['fields']['pen'] = {
                'quarter': p[64 + 11],
                'main_touch_columns': p[48], 'main_touch_rows': p[49],
                'stylus_columns_1': p[64 + 7], 'stylus_rows_1': p[64 + 8],
                'stylus_columns_2': p[64 + 9], 'stylus_rows_2': p[64 + 10],
                'frequency_request': request if request <= 63 and (request & 15) <= 3 else None,
            }
    return row


def changes(stream, *, deadline=None):
    """A bounded streaming reader; a live partial record is held until complete."""
    previous = None
    last_sequence = last_stamp = None
    first_stamp = None

    def read(size):
        data = bytearray()
        while len(data) < size:
            if deadline is not None and time.monotonic() >= deadline:
                return None
            block = stream.read(size - len(data))
            if block:
                data.extend(block)
            elif deadline is not None:
                time.sleep(.05)
            elif data:
                raise ValueError('truncated NTP1 record')
            else:
                return None
        return bytes(data)

    while (header := read(HEADER.size)) is not None:
        magic, size, length, _, _, _, flags, _ = HEADER.unpack(header)
        if magic != 0x3150544e or size != HEADER.size or not PREFIX + 64 <= length <= MAX_FRAME or flags & ~3:
            raise ValueError('invalid NTP1 header')
        frame = read(length)
        if frame is None:
            if deadline is None:
                raise ValueError('truncated NTP1 record')
            return
        row = decode(header, frame)
        gap = last_sequence is not None and row['sequence'] != last_sequence + 1
        if last_stamp is not None and row['boottime_ns'] <= last_stamp:
            raise ValueError('non-increasing NTP1 timestamp')
        first_stamp = row['boottime_ns'] if first_stamp is None else first_stamp
        row['elapsed_s'] = (row['boottime_ns'] - first_stamp) / 1e9
        row['sequence_gap'] = gap
        current = row.get('fields')
        if current != previous or row['epoch'] or gap or not row['valid']:
            yield row
        previous = current
        last_sequence, last_stamp = row['sequence'], row['boottime_ns']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path, help='existing regular --record file, never /dev/nvt-thp-*')
    parser.add_argument('--follow-seconds', type=float, help='follow this growing recording for a bounded interval')
    args = parser.parse_args()
    if args.follow_seconds is not None and not 0 < args.follow_seconds <= 3600:
        parser.error('--follow-seconds must be in (0, 3600]')
    if not stat.S_ISREG(args.capture.stat().st_mode):
        parser.error('only existing regular recording files are accepted')
    deadline = time.monotonic() + args.follow_seconds if args.follow_seconds else None
    try:
        with args.capture.open('rb') as stream:
            for row in changes(stream, deadline=deadline):
                print(json.dumps(row, separators=(',', ':')), flush=True)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
