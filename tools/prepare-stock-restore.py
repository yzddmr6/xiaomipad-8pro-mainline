#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate local regular-file GPT backups and prepare stock fragments offline.

The write/readback sequence was exercised on one piano 512 GB unit. This
parameterized extractor is offline-tested, not an on-device flasher. It issues
no device reads/writes, subprocesses, network requests or mounts. Nonregular
input paths are rejected before open, and the opened identity is rechecked.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import uuid
import zlib

MAX_BACKUP = 16 * 1024 * 1024
FACTORY_SHA256 = '8610cf859ab4fdda40acaa81c1c25ef27b233d8622df716587e7c518360ccb10'
LINUX_FS = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path):
    """Reject nonregular paths before open and recheck the opened identity."""
    before = os.lstat(path)
    require(stat.S_ISREG(before.st_mode), 'input must be a regular file')
    require(0 < before.st_size <= MAX_BACKUP, 'input size is outside the backup limit')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode), 'input must be a regular file')
        require((info.st_dev, info.st_ino, info.st_size) == (before.st_dev, before.st_ino, before.st_size),
                'input identity changed before open')
        require(0 < info.st_size <= MAX_BACKUP, 'input size is outside the backup limit')
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(MAX_BACKUP + 1)
        require(len(data) == info.st_size, 'input changed or is too large')
        return data


def header(fragment, offset, fragment_lba, block_size, disk_blocks, expected_guid):
    require(0 <= offset <= len(fragment) - block_size, 'header outside input')
    raw = fragment[offset:offset + block_size]
    require(raw[:8] == b'EFI PART', 'bad GPT signature')
    revision, size, crc, reserved = struct.unpack_from('<IIII', raw, 8)
    require(revision == 0x10000 and size == 92 and reserved == 0, 'unsupported GPT header')
    copy = bytearray(raw[:size])
    copy[16:20] = bytes(4)
    require(zlib.crc32(copy) & 0xffffffff == crc, 'header CRC mismatch')
    own, alternate, first, last = struct.unpack_from('<QQQQ', raw, 24)
    require((own, alternate) in ((1, disk_blocks - 1), (disk_blocks - 1, 1)), 'wrong header LBA pair')
    guid = str(uuid.UUID(bytes_le=raw[56:72]))
    entries_lba, count, stride, array_crc = struct.unpack_from('<QIII', raw, 72)
    require(guid == str(uuid.UUID(expected_guid)), 'disk GUID mismatch')
    require(2 <= first <= last < disk_blocks - 1, 'usable range outside disk')
    require(0 < count <= 4096 and stride == 128, 'unsupported entry geometry')
    start = (entries_lba - fragment_lba) * block_size
    require(0 <= start and start + count * stride <= len(fragment), 'entries outside backup')
    array = fragment[start:start + count * stride]
    require(zlib.crc32(array) & 0xffffffff == array_crc, 'entry-array CRC mismatch')
    return dict(own=own, alternate=alternate, first=first, last=last, guid=guid,
                entries_lba=entries_lba, count=count, stride=stride, array=array)


def partition_entries(array):
    entries = []
    guids = set()
    for number, off in enumerate(range(0, len(array), 128), 1):
        raw = array[off:off + 128]
        if raw[:16] == bytes(16):
            require(raw == bytes(128), 'nonzero data in unused entry')
            continue
        unique = raw[16:32]
        require(unique != bytes(16) and unique not in guids, 'missing or duplicate partition GUID')
        guids.add(unique)
        first, last, attrs = struct.unpack_from('<QQQ', raw, 32)
        require(first <= last, 'inverted partition range')
        name = raw[56:128].decode('utf-16-le').split('\0', 1)[0]
        entries.append(dict(number=number, first=first, last=last, attrs=attrs,
                            name=name, type=raw[:16], unique=unique, raw=raw))
    ordered = sorted(entries, key=lambda item: item['first'])
    require(all(a['last'] < b['first'] for a, b in zip(ordered, ordered[1:])), 'overlapping partitions')
    require(len({e['name'] for e in entries}) == len(entries), 'duplicate partition names')
    return entries


def parse_pair(head, tail, block_size, disk_blocks, expected_guid):
    require(block_size == 4096, 'only the observed 4096-byte logical-block layout is supported')
    require(disk_blocks > 128, 'invalid disk block count')
    require(len(head) % block_size == len(tail) % block_size == 0, 'unaligned backup length')
    tail_lba = disk_blocks - len(tail) // block_size
    primary = header(head, block_size, 0, block_size, disk_blocks, expected_guid)
    backup = header(tail, len(tail) - block_size, tail_lba, block_size, disk_blocks, expected_guid)
    require((primary['own'], primary['alternate']) == (1, disk_blocks - 1), 'wrong primary LBA pair')
    require((backup['own'], backup['alternate']) == (disk_blocks - 1, 1), 'wrong backup LBA pair')
    for key in ('first', 'last', 'guid', 'count', 'stride', 'array'):
        require(primary[key] == backup[key], 'primary/backup mismatch: ' + key)
    array_blocks = (primary['count'] * 128 + block_size - 1) // block_size
    require(primary['entries_lba'] >= 2 and primary['entries_lba'] + array_blocks <= primary['first'],
            'primary entries overlap usable storage')
    require(backup['entries_lba'] > backup['last'] and backup['entries_lba'] + array_blocks <= disk_blocks - 1,
            'backup entries overlap usable storage or its header')
    head_bytes = primary['first'] * block_size
    tail_bytes = (disk_blocks - 1 - primary['last']) * block_size
    require(head_bytes <= len(head) and tail_bytes <= len(tail), 'backup does not contain complete reserved region')
    # A PMBR may use the factory's saturated size rather than the actual last LBA.
    require(head[510:512] == b'\x55\xaa', 'missing protective MBR signature')
    mbr = head[446:462]
    require(mbr[0] == 0 and mbr[4] == 0xee, 'not a protective MBR')
    start, length = struct.unpack_from('<II', mbr, 8)
    require(start == 1 and length in (min(disk_blocks - 1, 0xffffffff), 0xffffffff), 'unexpected protective MBR range')
    require(head[462:510] == bytes(48), 'hybrid MBR is unsupported')
    entries = partition_entries(primary['array'])
    require(all(primary['first'] <= e['first'] <= e['last'] <= primary['last'] for e in entries), 'partition outside usable range')
    return dict(header=primary, entries=entries, head=head[:head_bytes], tail=tail[-tail_bytes:],
                head_blocks=head_bytes // block_size, tail_blocks=tail_bytes // block_size,
                tail_start=disk_blocks - tail_bytes // block_size)


def compare_layout(stock, current):
    """Only userdata shrinking plus the two documented Linux entries may differ."""
    for key in ('first', 'last', 'guid', 'count', 'stride', 'entries_lba'):
        require(stock['header'][key] == current['header'][key], 'stock/current geometry mismatch: ' + key)
    users = [e for e in stock['entries'] if e['name'] == 'userdata']
    require(len(users) == 1, 'stock userdata is not unique')
    userdata = users[0]
    require(userdata['last'] == stock['header']['last'], 'stock userdata does not reach the last usable LBA')
    original = {e['number']: e for e in stock['entries']}
    now = {e['number']: e for e in current['entries']}
    require(all(e['last'] < userdata['first'] for e in stock['entries'] if e is not userdata),
            'stock contains a partition after userdata starts')
    for number, entry in original.items():
        require(number in now, 'original partition disappeared')
        candidate = now[number]
        if number != userdata['number']:
            require(entry['raw'] == candidate['raw'], 'non-userdata original partition changed')
        else:
            a, b = bytearray(entry['raw']), bytearray(candidate['raw'])
            a[40:48] = b[40:48] = bytes(8)
            require(a == b and candidate['last'] < entry['last'], 'userdata changed beyond shrinking its end')
    added = [e for n, e in now.items() if n not in original]
    require(len(added) == 2 and {e['name'] for e in added} == {'linux_rootfs', 'linux_data'}, 'unexpected added partitions')
    root = next(e for e in added if e['name'] == 'linux_rootfs')
    data = next(e for e in added if e['name'] == 'linux_data')
    require(all(e['type'] == LINUX_FS and e['attrs'] == 0 for e in added), 'unexpected Linux type or attributes')
    require(now[userdata['number']]['last'] + 1 == root['first'] and root['last'] + 1 == data['first']
            and data['last'] == userdata['last'], 'Linux entries do not exactly fill the former userdata tail')
    # Entry-array padding, GPT-reserved gaps and MBR CHS/length are not user partitions.
    return dict(userdata_number=userdata['number'], userdata_start=userdata['first'],
                userdata_end=userdata['last'], original_partitions=len(original),
                current_partitions=len(now), removed_linux_entries=[root['number'], data['number']])


def fastboot_size(value):
    """Fastboot partition-size is hexadecimal, also without an 0x prefix."""
    require(bool(re.fullmatch(r'(?:0[xX])?[0-9a-fA-F]+', value)), 'invalid Fastboot size')
    return int(value, 16)


def fastboot_succeeded(returncode, output):
    return returncode == 0 and not re.search(r'\b(?:FAILED|FAILURE|ERROR)\b', output, re.I)


def factory_actions(source):
    """Plan only for the exact factory script observed in OS3.303 and OS4.5."""
    require(digest(source) == FACTORY_SHA256, 'factory script is not the audited script')
    actions = [dict(action='erase', target='userdata', condition='explicit full-data-wipe authorization')]
    for number, raw in enumerate(source.decode('utf-8').splitlines(), 1):
        line = raw.strip()
        if not re.match(r'^fastboot \$\* (flash|erase|set_active|reboot)(?:\s|$)', line):
            continue
        match = re.fullmatch(r'fastboot \$\* (flash|erase|set_active|reboot)(?:\s+(\S+))?(?:\s+`dirname \$0`/images/(\S+))?', line)
        require(match is not None, 'unrecognized factory action')
        action, target, image = match.groups()
        actions.append(dict(action=action, target=target, image=image, source_line=number,
                            condition='getvar crc is explicitly 1' if number in (17, 19) else 'previous commands succeeded'))
    require(len(actions) == 56, 'unexpected factory command count')
    return actions


def prepare(args):
    stock = parse_pair(read_regular(args.stock_head), read_regular(args.stock_tail), args.block_size, args.disk_blocks, args.expected_disk_guid)
    current = parse_pair(read_regular(args.current_head), read_regular(args.current_tail), args.block_size, args.disk_blocks, args.expected_disk_guid)
    layout = compare_layout(stock, current)
    report = dict(state='VALIDATED_OFFLINE_NOT_DEVICE_AUTHORIZATION', block_size=args.block_size,
                  disk_blocks=args.disk_blocks, expected_disk_guid=args.expected_disk_guid,
                  head_blocks=stock['head_blocks'], tail_blocks=stock['tail_blocks'], tail_start_lba=stock['tail_start'],
                  layout=layout, userdata_bytes=(layout['userdata_end'] - layout['userdata_start'] + 1) * args.block_size,
                  files={name: dict(bytes=len(data), sha256=digest(data)) for name, data in
                         [('stock-primary.bin', stock['head']), ('stock-backup.bin', stock['tail']),
                          ('current-primary.bin', current['head']), ('current-backup.bin', current['tail'])]},
                  write_order=['stock-backup.bin', 'readback-backup-and-verify', 'stock-primary.bin', 'readback-primary-and-verify'],
                  warning='No device was inspected. Check identity, RAM root, unmounted UFS, swaps, holders, power and Fastboot readiness before a separately authorized write.')
    if args.factory_script:
        report['factory_plan'] = factory_actions(read_regular(args.factory_script))
    if args.output:
        output = Path(args.output)
        output.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, data in [('stock-primary.bin', stock['head']), ('stock-backup.bin', stock['tail']),
                           ('current-primary.bin', current['head']), ('current-backup.bin', current['tail'])]:
            with (output / name).open('xb') as stream:
                os.chmod(output / name, 0o600)
                stream.write(data)
        with (output / 'plan.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
        with (output / 'SHA256SUMS').open('x', encoding='utf-8') as stream:
            for name, info in report['files'].items():
                stream.write(info['sha256'] + '  ' + name + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('stock-head', 'stock-tail', 'current-head', 'current-tail', 'expected-disk-guid'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--block-size', type=int, required=True)
    parser.add_argument('--disk-blocks', type=int, required=True)
    parser.add_argument('--factory-script', help='optional audited flash_all.sh; plan only, never run')
    parser.add_argument('--output', help='new directory; omit for validation without filesystem writes')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args), indent=2))
    except (ValueError, OSError, UnicodeError, struct.error) as exc:
        parser.exit(1, f'refused: {exc}\n')


if __name__ == '__main__':
    main()
