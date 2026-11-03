#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Root-only, exclusive piano dual-camera NV12 capture; run only on the tablet.

This test owns a temporary loopback module
and daemon, saves the CAMSS state they change, and restores it in finally.
SIGINT/TERM are handled; SIGKILL or loss of power cannot run cleanup.
"""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import struct
import subprocess
import tempfile
import time

WIDTH, HEIGHT, COUNT = 1920, 1440, 30
FRAME_SIZE = WIDTH * HEIGHT * 3 // 2
USER = 0x00980900
EXPOSURE, AGAIN, DGAIN, VBLANK = USER + 17, 0x009e0903, 0x009f0905, 0x009e0901
HFLIP, VFLIP, CCM = USER + 20, USER + 21, USER | 0x1ff0
FOCUS = 0x009a090a
# piano21 dw9768.c (fc16bcb2fef9): default move delay 6.65 ms (+1 ms
# scheduling allowance); GT9764 power/init waits run synchronously on open.
# Community camerad a75f8c5 uses AF_SETTLE=2 frames at 30 Hz (~67 ms).
# Use 100 ms after each S_CTRL, then start a fresh stream (no old queued frames).
# This is a conservative test allowance, not a measured GT9764 settling time.
FOCUS_SETTLE_SECONDS = 0.100
FOCUS_FRAMES = 3
FOCUS_RATIO = 1.3
# Community gradient energy / luma, in 8-bit units. At mean 128, independent
# noise with sigma=8 gives ~1.0; reject scores below this conservative texture
# gate. This is a test threshold, not calibrated sensor noise or AF tuning.
FOCUS_TEXTURE_FLOOR = 1.0
CFG = {'rear': ('s5kjn1', 'msm_csiphy1', 'msm_csid0', 'msm_vfe0_pix', 'msm_vfe0_video3'),
       'front': ('ov32d40', 'msm_csiphy4', 'msm_csid1', 'msm_vfe1_pix', 'msm_vfe1_video3')}


def command(*args, timeout=10):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout


def ioctl(dev, nr, data):
    # asm-generic _IOWR('V', nr, sizeof(struct)); arm64's V4L2 UAPI.
    buf = bytearray(data)
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        fcntl.ioctl(fd, 0xc0000000 | len(buf) << 16 | ord('V') << 8 | nr, buf)
    finally:
        os.close(fd)
    return bytes(buf)


def ctrl(dev, cid, value=None):
    b = ioctl(dev, 27 if value is None else 28, struct.pack('=Ii', cid, value or 0))
    return struct.unpack('=Ii', b)[1]


def ccm(dev, values=None):
    payload = (ctypes.c_int32 * 9)(*(values or [0] * 9))
    ext = ctypes.create_string_buffer(struct.pack('=IIIQ', CCM, 36, 0, ctypes.addressof(payload)))
    data = struct.pack('=IIIII4xQ', 0, 1, 0, 0, 0, ctypes.addressof(ext))
    ioctl(dev, 71 if values is None else 72, data)
    return list(payload)


def request(size, *words):
    b = bytearray(size)
    struct.pack_into('=' + 'I' * len(words), b, 0, *words)
    return b


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def nodes():
    return {p.joinpath('name').read_text().strip(): '/dev/' + p.name
            for p in Path('/sys/class/video4linux').glob('*') if p.joinpath('name').exists()}


def find(table, prefix):
    hits = [(n, d) for n, d in table.items() if n == prefix or n.startswith(prefix + ' ')]
    if len(hits) != 1:
        raise RuntimeError(f'{prefix}: expected one bound entity, found {len(hits)}')
    return hits[0][1]


def links(topology):
    result, entity, pad = [], None, None
    for line in topology.splitlines():
        m = re.match(r'- entity \d+: (.+) \(', line)
        if m:
            entity = m[1]
        m = re.search(r'pad(\d+):', line)
        if m:
            pad = int(m[1])
        m = re.search(r'-> "(.+)":(\d+) \[(.*)\]', line)
        if m and 'IMMUTABLE' not in m[3]:
            if entity is None or pad is None:
                raise RuntimeError('Cannot parse media topology')
            result.append((f'"{entity}":{pad}->"{m[1]}":{m[2]}', int('ENABLED' in m[3])))
    return result


def snapshot(table, name):
    state = {'controls': [], 'formats': [], 'ccm': [], 'warnings': []}
    for sensor, phy, csid, vfe, video in [CFG[name]]:
        sd, vd = find(table, sensor), find(table, vfe)
        # Refuse a sensor test pattern; generated patterns cannot pass a scene test.
        if ctrl(sd, 0x009f0903):
            raise RuntimeError(sensor + ': test pattern is enabled')
        flips = [HFLIP, VFLIP] if name == 'rear' else []  # OV32D40 has no flip controls.
        for dev, controls in [(sd, flips + [VBLANK, EXPOSURE, AGAIN]),
                              (vd, [DGAIN, USER+14, USER+15, USER+2, USER+1])]:
            for cid in controls:
                state['controls'].append([dev, cid, ctrl(dev, cid)])
        state['ccm'].append([vd, ccm(vd)])
        # Sink S_FMT propagates to source pads, including unused CSID RDI pads.
        for entity, pads in [(sensor, [0]), (phy, [0, 1]), (csid, [0, 1, 2, 3, 4]), (vfe, [0, 1])]:
            for pad in pads:
                dev = find(table, entity)
                state['formats'].append([dev, 4, 5, ioctl(dev, 4, request(88, 1, pad)).hex()])
                # Restore the VFE compose rectangle before its source pad format.
                if entity == vfe and pad == 0:
                    state['formats'].append([dev, 61, 62, ioctl(dev, 61, request(64, 1, 0, 0x100)).hex()])
                    state['formats'].append([dev, 61, 62, ioctl(dev, 61, request(64, 1, 1, 0)).hex()])
        dev = find(table, video)
        state['formats'].append([dev, 4, 5, ioctl(dev, 4, request(208, 9)).hex()])
    lens = [d for n, d in table.items() if n.startswith('dw9768')]
    if name == 'rear' and len(lens) == 1:
        try:
            state['controls'].append([lens[0], FOCUS, ctrl(lens[0], FOCUS)])
        except OSError as e:
            state['warnings'].append(f'Optional VCM unavailable: {e}; AF is not tested')
    return state


def restore(state, media, saved_links):
    errors = []
    def attempt(label, action):
        try:
            action()
        except Exception as e:
            errors.append(f'{label}: {e}')
    # Attempt every restoration even if one ioctl fails; never hide partial cleanup.
    attempt('reset links', lambda: command('media-ctl', '-d', media, '-r'))
    for link, enabled in saved_links:
        if enabled:
            attempt(link, lambda: command('media-ctl', '-d', media, '-l', f'{link}[1]'))
    for dev, cid, value in state['controls']:
        if cid in (HFLIP, VFLIP):
            attempt(f'{dev} flip', lambda: ctrl(dev, cid, value))
    for dev, get, put, value in state['formats']:
        data = bytes.fromhex(value)
        attempt(f'{dev} format', lambda: ioctl(dev, put, data))
    for dev, cid, value in state['controls']:
        attempt(f'{dev} control {cid:#x}', lambda: ctrl(dev, cid, value))
    for dev, values in state['ccm']:
        attempt(f'{dev} CCM', lambda: ccm(dev, values))
    # Read back after all coupled format/control changes have completed.
    for dev, get, put, value in state['formats']:
        data = bytes.fromhex(value)
        try:
            if ioctl(dev, get, data) != data:
                errors.append(f'{dev}: format/selection differs')
        except OSError as e:
            errors.append(str(e))
    for dev, cid, value in state['controls']:
        try:
            if ctrl(dev, cid) != value:
                errors.append(f'{dev}: control {cid:#x} differs')
        except OSError as e:
            errors.append(str(e))
    for dev, values in state['ccm']:
        try:
            if ccm(dev) != values:
                errors.append(f'{dev}: CCM differs')
        except OSError as e:
            errors.append(str(e))
    if links(command('media-ctl', '-d', media, '-p')) != saved_links:
        errors.append('Media links differ')
    if errors:
        raise RuntimeError('; '.join(errors))


def stop(proc):
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise RuntimeError('Process required SIGKILL; inspect cleanup and kernel log')


def exclusive(devices):
    for proc in Path('/proc').glob('[0-9]*'):
        if proc.name == str(os.getpid()):
            continue
        for fd in proc.joinpath('fd').glob('*'):
            try:
                if os.readlink(fd) in devices:
                    raise RuntimeError(f'Camera device already in use by PID {proc.name}')
            except (FileNotFoundError, PermissionError):
                continue


def capture(name, output, table, logpath):
    import numpy as np
    sensor, _, _, vfe, _ = CFG[name]
    folder = output / name
    folder.mkdir()
    result = {'status': 'FAIL', 'frames': [], 'width': WIDTH, 'height': HEIGHT,
              'format': 'NV12', 'luma_range': 'full (community JPEG colorspace)',
              'metadata_scope': 'controls sampled after receipt; not frame-synchronous sensor metadata',
              'analogue_gain_unity': 32 if name == 'rear' else 256,
              'isp_digital_gain_unity': 1024,
              'bootstrap_frames_discarded': 0}
    proc = None
    try:
        loop = find(nodes(), name.title() + ' Camera')
        fmt = ioctl(loop, 4, request(208, 1))
        w, h, fourcc, field, stride, size = struct.unpack_from('=IIIIII', fmt, 8)
        if (w, h, fourcc, stride, size) != (WIDTH, HEIGHT, int.from_bytes(b'NV12', 'little'), WIDTH, FRAME_SIZE):
            raise RuntimeError('Loopback format/stride differs from the community NV12 contract')
        with (folder / 'v4l2-ctl.log').open('wb') as err:
            proc = subprocess.Popen(['v4l2-ctl', '-d', loop, '--silent', '--stream-mmap=4',
                                     '--stream-to=-'], stdout=subprocess.PIPE, stderr=err)
            with selectors.DefaultSelector() as sel:
                sel.register(proc.stdout, selectors.EVENT_READ)
                pending = bytearray()
                deadline = time.monotonic() + 30
                started = False
                while len(result['frames']) < COUNT:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('30-second capture deadline')
                    if not sel.select(min(1, max(0, deadline-time.monotonic()))):
                        continue
                    data = os.read(proc.stdout.fileno(), 1024*1024)
                    if not data:
                        raise RuntimeError('Capture stream closed before 30 frames')
                    pending.extend(data)
                    while len(pending) >= FRAME_SIZE and len(result['frames']) < COUNT:
                        frame = bytes(pending[:FRAME_SIZE])
                        del pending[:FRAME_SIZE]
                        y = np.frombuffer(frame, dtype=np.uint8, count=WIDTH*HEIGHT)
                        mean, std = float(y.mean()), float(y.std())
                        # The daemon deliberately writes an initial black frame. Discard only
                        # its startup period, bounded above, before counting the 30 scene frames.
                        if not started and (name + ': streaming ' not in logpath.read_text() or
                                            (not np.any(y) and frame[WIDTH*HEIGHT:] == bytes([128]) * (FRAME_SIZE-WIDTH*HEIGHT))):
                            if not result['bootstrap_frames_discarded']:
                                (folder / 'bootstrap.nv12').write_bytes(frame)
                            result['bootstrap_frames_discarded'] += 1
                            continue
                        started = True
                        path = folder / f'{len(result["frames"]):02d}.nv12'
                        path.write_bytes(frame)
                        result['frames'].append({'path': str(path), 'sha256': sha(path),
                            'bytes': len(frame), 'luma_mean': mean, 'luma_stddev': std,
                            # JPEG NV12 uses full-range luma; 16 is not its black level.
                            'nonblack_nonconstant': mean > 1 and std > 1,
                            'controls_monotonic_ns': time.monotonic_ns(),
                            'exposure_lines': ctrl(find(table, sensor), EXPOSURE),
                            'analogue_gain': ctrl(find(table, sensor), AGAIN),
                            'isp_digital_gain': ctrl(find(table, vfe), DGAIN)})
        result['distinct_frames'] = len({f['sha256'] for f in result['frames']})
        if len(result['frames']) == COUNT and result['distinct_frames'] > 1 and all(f['nonblack_nonconstant'] for f in result['frames']):
            result['status'] = 'PASS'
    except InterruptedError:
        raise
    except Exception as e:
        result['error'] = str(e)
    finally:
        try:
            stop(proc)
        except Exception as e:
            result.update(status='FAIL', cleanup_error=str(e))
    return result


def focus_positions(minimum, maximum, step):
    """Five distinct, evenly spaced legal steps including both legal endpoints."""
    if step <= 0 or maximum < minimum:
        raise ValueError('Invalid focus control range/step')
    steps = (maximum - minimum) // step
    if steps < 4:
        raise ValueError('Focus control has fewer than five positions')
    return [minimum + ((i * steps + 2) // 4) * step for i in range(5)]


def focus_sharpness(plane):
    """Community af_sharpness(): centre-third horizontal energy / luma.

    Input is one 8-bit intensity plane: NV12 Y on the tablet. A Bayer green
    sublattice can exercise the same calculation offline, without demosaicing.
    Cast before subtraction so uint8 data cannot wrap.
    """
    import numpy as np
    plane = np.asarray(plane, dtype=np.float64)
    if plane.ndim != 2 or min(plane.shape) < 12 or not np.isfinite(plane).all():
        raise ValueError('Sharpness needs a finite 2D intensity plane >=12x12')
    h, w = plane.shape
    left = plane[h//3:2*h//3:4, w//3:2*w//3:2]
    right = plane[h//3:2*h//3:4, w//3+2:2*w//3+2:2]
    luma = float(left.sum())
    return float(np.square(right-left).sum() / luma) if luma > 0 else 0.0


def focus_verdict(samples):
    import math
    if len(samples) != 5 or any(s.get('error') or
            s.get('readback') != s['position'] or
            s.get('readback_after') != s['position'] for s in samples):
        return {'status': 'FAIL', 'reason': 'Focus write/readback or capture failed'}
    values = [s['sharpness'] for s in samples]
    if not all(math.isfinite(v) and v >= 0 for v in values):
        return {'status': 'FAIL', 'reason': 'Invalid sharpness evidence'}
    low, high = min(values), max(values)
    evidence = {'sharpness_min': low, 'sharpness_max': high,
                'sharpness_ratio': high / low if low else None,
                'ratio_is_infinite': low == 0 and high > 0}
    if high < FOCUS_TEXTURE_FLOOR:
        return dict(evidence, status='INCOMPLETE', reason='Insufficient scene texture above noise threshold')
    if high >= FOCUS_RATIO * low:
        return dict(evidence, status='PASS', reason='Focus-dependent image sharpness changed')
    return dict(evidence, status='FAIL', reason='执行器写入被接受但图像无变化')


def focus_sweep(output, table, state):
    """Run after stopping our daemon: AF/AE must not compete with the sweep.

    Read the same CAMSS NV12 buffers camerad normally copies to loopback. Keep
    the lens fd open through all positions to prevent runtime-PM parking. The
    pre-daemon focus value stays in state and is restored by the common finally.
    """
    import numpy as np
    result = {'status': 'FAIL', 'samples': [], 'settle_seconds': FOCUS_SETTLE_SECONDS,
              'settle_source': 'dw9768.c fc16bcb2fef9; camerad a75f8c5 AF_SETTLE=2 at 30 Hz',
              'metric': 'community centre-third horizontal gradient energy / luma (8-bit)',
              'texture_floor': FOCUS_TEXTURE_FLOOR, 'required_ratio': FOCUS_RATIO,
              'frames_per_position': FOCUS_FRAMES, 'startup_frames_skipped': 4,
              'format': 'NV12', 'capture_source': 'CAMSS, after camerad stopped; AE/AWB/AF fixed',
              'metadata_scope': 'controls before/after capture, not frame-synchronous'}
    fd = None
    try:
        lens = find(table, 'dw9768')
        saved = [v for dev, cid, v in state['controls'] if dev == lens and cid == FOCUS]
        if len(saved) != 1:
            raise RuntimeError('Focus control missing/unreadable in original snapshot; cannot safely restore')
        result.update(device=lens, original_position=saved[0])
        exclusive(set(table.values()))
        fd = os.open(lens, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        query = ioctl(lens, 36, request(68, FOCUS))  # struct v4l2_queryctrl
        cid, kind, label, minimum, maximum, step, default, flags = struct.unpack_from('=II32siiiiI', query)
        if cid != FOCUS or kind != 1 or flags & (0x1 | 0x4 | 0x10):
            raise RuntimeError('Focus control is not an active, writable integer')
        result['control'] = {'minimum': minimum, 'maximum': maximum, 'step': step,
                             'name': label.rstrip(b'\0').decode(errors='replace'), 'flags': flags}
        positions = focus_positions(minimum, maximum, step)
        result['positions'] = positions
        sensor, _, _, vfe, video = CFG['rear']
        video = find(table, video)
        fmt = ioctl(video, 4, request(208, 9))  # VIDEO_CAPTURE_MPLANE
        width, height, fourcc = struct.unpack_from('=III', fmt, 8)
        size, stride = struct.unpack_from('=II', fmt, 28)
        if ((width, height, fourcc, fmt[188]) !=
                (WIDTH, HEIGHT, int.from_bytes(b'NV12', 'little'), 1) or
                stride < width or size != stride * height * 3 // 2):
            raise RuntimeError('Rear CAMSS is not the configured single-plane NV12 stream')
        result.update(width=width, height=height, stride=stride, frame_bytes=size, video_device=video)
        folder = output / 'rear' / 'focus-sweep'
        folder.mkdir()
        def metadata():
            return {'exposure_lines': ctrl(find(table, sensor), EXPOSURE),
                    'analogue_gain': ctrl(find(table, sensor), AGAIN),
                    'isp_digital_gain': ctrl(find(table, vfe), DGAIN)}
        fixed = metadata()
        result['fixed_controls'] = fixed
        for position in positions:
            sample = {'position': position, 'frames': []}
            result['samples'].append(sample)
            try:
                sample['set_monotonic_ns'] = time.monotonic_ns()
                sample['accepted'] = ctrl(lens, FOCUS, position)
                sample['readback'] = ctrl(lens, FOCUS)
                if sample['accepted'] != position or sample['readback'] != position:
                    raise RuntimeError('Focus S_CTRL/G_CTRL differs from requested position')
                time.sleep(FOCUS_SETTLE_SECONDS)
                sample['controls_before'] = metadata()
                # Each stream starts after settling: there are no buffers from a
                # previous position. Skip the first four pipeline startup frames.
                with (folder / f'{position}-v4l2-ctl.log').open('wb') as log:
                    frames = subprocess.run(['v4l2-ctl', '-d', video, '--silent',
                        '--stream-mmap=4', f'--stream-count={FOCUS_FRAMES + 4}',
                        '--stream-to=-'], check=True, stdout=subprocess.PIPE, stderr=log, timeout=15).stdout
                if len(frames) != size * (FOCUS_FRAMES + 4):
                    raise RuntimeError(f'Unexpected capture length: {len(frames)}')
                frames = frames[4*size:]
                sample['readback_after'] = ctrl(lens, FOCUS)
                sample['controls_after'] = metadata()
                sample['capture_monotonic_ns'] = time.monotonic_ns()
                for index in range(FOCUS_FRAMES):
                    frame = frames[index*size:(index+1)*size]
                    path = folder / f'{position}-{index}.nv12'
                    path.write_bytes(frame)
                    y = np.frombuffer(frame, dtype=np.uint8, count=stride*height).reshape(height, stride)[:, :width]
                    sample['frames'].append({'path': str(path), 'sha256': sha(path),
                        'sharpness': focus_sharpness(y), 'luma_mean': float(y.mean()),
                        'luma_stddev': float(y.std())})
                sample['sharpness'] = float(np.median([f['sharpness'] for f in sample['frames']]))
                if sample['controls_before'] != fixed or sample['controls_after'] != fixed:
                    raise RuntimeError('Exposure/gain changed during focus sweep')
                if sample['readback_after'] != position:
                    raise RuntimeError('Focus changed during capture')
            except InterruptedError:
                raise
            except Exception as e:
                sample['error'] = str(e)
                raise
        result.update(focus_verdict(result['samples']))
    except InterruptedError:
        raise
    except Exception as e:
        result.update(status='FAIL', reason=str(e))
    finally:
        if fd is not None:
            os.close(fd)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True, help='New evidence directory (mode 0700)')
    p.add_argument('--expected-release', required=True)
    p.add_argument('--focus-sweep', action='store_true',
                   help='Rear only: five focus positions; keep a lit, textured scene and camera stationary')
    a = p.parse_args()
    os.umask(0o077)
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {'kernel_release': os.uname().release, 'status': 'FAIL',
              'scope': '30 NV12 frames per camera; not image quality, motion, AE/AF accuracy or application acceptance',
              'cameras': {n: {'status': 'FAIL', 'error': 'not reached'} for n in CFG},
              'cleanup': []}
    if a.focus_sweep:
        result['scope'] += '; rear focus sweep measures image change, not autofocus accuracy'
        result['cameras']['rear']['focus_sweep'] = {'status': 'FAIL', 'reason': 'not reached'}
    daemon, state, media, loaded = None, None, None, False
    changed = False
    lock = None
    def interrupted(signum, frame):
        raise InterruptedError(f'signal {signum}')
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        if os.geteuid() != 0 or os.uname().machine != 'aarch64':
            raise RuntimeError('Run as root on the ARM64 tablet')
        if b'xiaomi,piano\0' not in Path('/proc/device-tree/compatible').read_bytes():
            raise RuntimeError('Board compatible is not xiaomi,piano')
        if os.uname().release != a.expected_release:
            raise RuntimeError('Running kernel does not match --expected-release')
        lock = open('/run/lock/piano-camera-test.lock', 'w')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if Path('/sys/module/v4l2loopback').exists():
            raise RuntimeError('Existing loopback state: stop its owner and unload it before this exclusive test')
        if subprocess.run(['pgrep', '-x', 'piano-camerad'], stdout=subprocess.DEVNULL).returncode == 0:
            raise RuntimeError('An existing camerad owns the cameras')
        receipt = Path('/usr/share/doc/linux-image-' + os.uname().release + '/build-result.json')
        # RAM boot on an older installed root has no candidate package receipt.
        result['kernel_build'] = json.loads(receipt.read_text()) if receipt.is_file() else None
        if result['kernel_build'] and result['kernel_build']['kernel_release'] != os.uname().release:
            raise RuntimeError('Installed kernel receipt does not match the running release')
        result['kernel_version'] = os.uname().version
        result['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        result['dtb_sha256'] = sha(Path('/sys/firmware/fdt'))
        result['daemon_sha256'] = sha(Path('/usr/lib/piano/piano-camerad'))
        result['test_sha256'] = sha(Path(__file__))
        (output / 'dmesg-before.txt').write_text(command('dmesg'))
        result['modules_before'] = Path('/proc/modules').read_text()
        group = Path('/sys/bus/platform/devices/ad27000.isp/iommu_group/type')
        result['camss_iommu_type'] = group.read_text().strip()
        if result['camss_iommu_type'] not in ('DMA', 'DMA-FQ'):
            raise RuntimeError('CAMSS requires a translated DMA domain; no domain is changed by this test')
        candidates = []
        for dev in sorted(Path('/dev').glob('media*')):
            text = command('media-ctl', '-d', str(dev), '-p')
            (output / (dev.name + '-before.txt')).write_text(text)
            if re.search(r'driver\s+qcom-camss', text):
                candidates.append((str(dev), text))
        if len(candidates) != 1:
            raise RuntimeError('CAMSS graph absent/ambiguous; inspect probe logs')
        media, topology = candidates[0]
        table = nodes()
        exclusive(set(table.values()) | {media})
        saved_links = links(topology)
        state = {'controls': [], 'formats': [], 'ccm': [], 'warnings': []}
        ready = []
        for name in CFG:
            try:
                saved = snapshot(table, name)  # No setters until each snapshot is complete.
                for key in state:
                    state[key].extend(saved[key])
                ready.append(name)
            except InterruptedError:
                raise
            except Exception as e:
                result['cameras'][name] = {'status': 'FAIL', 'error': 'snapshot: ' + str(e)}
        (output / 'state-before.json').write_text(json.dumps({'state': state, 'links': saved_links}, indent=2)+'\n')
        if not ready:
            raise RuntimeError('Neither camera is ready for capture')
        if Path('/dev/video40').exists() or Path('/dev/video41').exists():
            raise RuntimeError('Loopback node numbers 40/41 are already occupied')
        command('modprobe', 'v4l2loopback')
        loaded = True
        command('udevadm', 'settle', '--timeout=5')
        with tempfile.TemporaryDirectory(prefix='piano-camera-', dir='/run') as tmp:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                notify.bind(tmp + '/notify')
                notify.settimeout(10)
                logpath = output / 'camerad.log'
                with logpath.open('wb') as log:
                    env = dict(os.environ, NOTIFY_SOCKET=tmp + '/notify',
                               PIANO_CAMERA_OUTPUT='nv12')
                    daemon = subprocess.Popen(['/usr/lib/piano/piano-camerad'], env=env,
                                              stdout=log, stderr=log)
                    changed = True
                    if b'READY=1' not in notify.recv(4096):
                        raise RuntimeError('Unexpected daemon readiness notification')
                    for name in ('front', 'rear'):
                        if name in ready:
                            result['cameras'][name] = capture(name, output, table, logpath)
                            # Save before restoration, including a failed STREAMON.
                            (output / (name + '-media-capture.txt')).write_text(
                                command('media-ctl', '-d', media, '-p'))
                    if a.focus_sweep:
                        # The unmodified community daemon has autonomous AF. Stop
                        # our daemon normally before taking control of the lens.
                        stop(daemon)
                        if result['cameras']['rear']['status'] == 'PASS':
                            sweep = focus_sweep(output, table, state)
                        else:
                            sweep = {'status': 'FAIL', 'reason': 'Rear capture unavailable for focus sweep'}
                        result['cameras']['rear']['focus_sweep'] = sweep
    except Exception as e:
        result['error'] = str(e)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        actions = [('stop camerad', lambda: stop(daemon))]
        if changed and state is not None:
            actions.append(('restore media formats, links and controls', lambda: restore(state, media, saved_links)))
        if loaded:
            actions.append(('remove owned loopback', lambda: command('rmmod', 'v4l2loopback')))
        for label, action in actions:
            try:
                action()
                result['cleanup'].append({'action': label, 'status': 'PASS'})
            except Exception as e:
                result['cleanup'].append({'action': label, 'status': 'FAIL', 'error': str(e)})
        if media:
            try:
                (output / 'media-after.txt').write_text(command('media-ctl', '-d', media, '-p'))
            except Exception as e:
                result['cleanup'].append({'action': 'media after', 'status': 'FAIL', 'error': str(e)})
        try:
            (output / 'dmesg-after.txt').write_text(command('dmesg'))
            result['modules_after'] = Path('/proc/modules').read_text()
        except Exception as e:
            result['dmesg_error'] = str(e)
        if a.focus_sweep:
            result['cameras']['rear'].setdefault('focus_sweep', {'status': 'FAIL', 'reason': 'not reached'})
        if not result.get('error') and all(c['status'] == 'PASS' for c in result['cameras'].values()) and all(c['status'] == 'PASS' for c in result['cleanup']):
            result['status'] = result['cameras']['rear']['focus_sweep']['status'] if a.focus_sweep else 'PASS'
        (output / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
        (output / 'SHA256SUMS').write_text(''.join(sha(f)+'  '+str(f.relative_to(output))+'\n'
                                                 for f in sorted(output.rglob('*')) if f.is_file() and f.name != 'SHA256SUMS'))
        if lock:
            lock.close()
    print(json.dumps({'status': result['status'], 'evidence': str(output / 'result.json')}))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
