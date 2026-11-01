# Piano THP Finger Input

This contains offline replay and a native live finger-input executable for NT36532E.
It reuses the Apache-2.0 processing core and filters from `xiaomi-sheng-thp`;
source identity and changes are recorded in the repository `NOTICE`.

The live software path is tested offline, including real Linux uinput/input/evdev
inside an ARM64 virtual machine. One supervised piano session streamed frames and
produced taps and swipes; held contacts still break up, and orientation (below,
corrected but not rerun), accuracy, multitouch, GNOME interaction and pen support
are not validated.
No Android libraries, firmware, electrode calibration or device records are shipped.

The pinned kernel now contains an experimental `nt36532e-thp` misc transport,
with its NTP1 contract and source provenance documented beside the driver.
Firmware parsing and simulated register downloads pass ARM64 KUnit with the
user-supplied piano image. The default board nodes do not enable it; only the
supervised diagnostic DTB variant does; a device test with it streamed 120 Hz frames
over physical SPI and IRQs. Neither this program nor a successful build enables
hardware in a default image.

## Build

Requires a C++20 compiler, Make and the nlohmann JSON development headers
(`nlohmann-json3-dev` on Ubuntu). The tested header version is 3.11.3.

```sh
make
make test
```

`CXX`, `CPPFLAGS`, `LDFLAGS`, `JSON_INCLUDE`, `OUT` and `RUNNER` can be supplied
for cross-compilation and host-side QEMU-user testing. No build step accesses a tablet.
The default tests do not open input devices. They cover synthetic calibration,
bounded NTP1 framing and contact lifecycle; they do not establish hardware touch.

## Private Inputs

The vendor configuration is not standard INI. The extraction tool uses a lexer
and literal parser for the required numeric assignments, and does not execute it.
Only separable 40-row/60-column electrode maps with the supported processing
units and symmetric edge parameters are currently accepted.

```sh
python3 ../../tools/extract-thp-calibration.py \
  --input /path/to/own/piano_nova_PANEL_thp_config.ini \
  --output /path/to/private/calibration.json
build/piano-thp-replay --stock-ring \
  /path/to/private/calibration.json /path/to/private/stock-records.bin
```

`PANEL` is `csot` or `boe`, the panel fitted to the unit. The output records the
file's `project_vendor_id` (4 for CSOT, 26 for BOE); the live program logs an error
when it does not match the panel the kernel reports.
Keep both inputs, the extracted calibration and resulting reports private.
The output uses 10 units per pixel; X follows electrode columns and Y follows
rows. These are processor axes, not a verified transformation to panel coordinates.
The filter consumes the same calibrated bounds in its transposed coordinate frame.

`build/piano-thp-replay --ntp1 CALIBRATION.json CAPTURE.ntp1` replays a private
`--record` capture, or a byte head/tail of one, through the same processor and prints
per-record scan mode, delta extremes, contacts and tracker state as JSON lines in
processor axes. It starts at the first NTP1 header, resets on sequence gaps and does
not process an incomplete final record. It needs no device.

The replayer reads the observed 24-byte Android record header and 5192-byte
payload, verifies the firmware header and declared checksum region, then orders
ring slots by sequence. Unsupported packet types, invalid checksum, duplicate
sequences or non-increasing timestamps are errors. Opaque trailing bytes are not
included in the firmware checksum and are not treated as finger matrix data.

The ten available real idle records establish packet decoding and reference
acquisition, not finger tracking. Only one record follows reference acquisition
in that sample; it produces no contacts. Positive gestures, edge calibration,
orientation, timing under load and the remaining upstream filter tuning still
need actual piano input evidence.

## Live Input

Only use this with the matching experimental kernel and board firmware. Opening
the kernel stream downloads touch firmware into controller RAM and starts IRQ
capture. Firmware selection/loading belongs to the kernel, not this executable.
The program does not change pen, doze or coordinate-reporting modes.

```sh
build/piano-thp-live /path/to/private/calibration.json /dev/nvt-thp-SPI_DEVICE
```

Supply the exact misc node; there is no device scanning or automatic restart.
The caller needs access to that node and `/dev/uinput`. There is no default or
enabled systemd unit, startup hook or installed calibration; `diag/` is only a
supervised RAM diagnostic (below).

The NTP1 reader buffers at most one 8223-byte record and handles partial reads.
Firmware checksums and finger geometry are validated separately; other packet
types are ignored, not passed through the finger algorithm. Sequence gaps,
epochs and invalid packets reset processing and release active contacts. The
reference service's 100 ms stall policy also bounds record age: pen-only traffic
does not keep a stale finger down, and old queued records do not replay clicks.
NTP1 timestamps must use the same CLOCK_BOOTTIME as the running Linux kernel.

Input ranges come from the supplied calibration, transformed to the panel by
`src/orientation.hpp`. In a supervised piano test (landscape, USB
port on the right) a one-finger swipe from the left edge to the right edge raised
processor X at constant Y, and a swipe from the top edge to the bottom edge lowered
processor Y at constant X. The device therefore reports `ABS_MT_POSITION_X = X`
along the 3200-pixel side (range `0..column_maximum - 1`) and
`ABS_MT_POSITION_Y = row_maximum - 1 - Y` along the 2136-pixel side (range
`0..row_maximum - 1`). The mapping follows the observed swipe directions.
This is a linear full-range mapping; edge linearity
and accuracy are unmeasured. That unit has the CSOT panel. On the BOE panel the
device reports `ABS_MT_POSITION_Y = Y` instead, without the reversal, following
the BOE unit test of liisoya/xiaomipad-8pro-mainline `8bbc68239c17` (see NOTICE).
The panel comes only from the kernel: the program reads
`/sys/class/misc/nvt-thp-SPI_DEVICE/device/panel_vendor` (`csot` or `boe`) for the
node it opens, and uses CSOT when a kernel does not export the attribute. Any
other value is logged as an error and also uses CSOT, so touch keeps working. If a later test contradicts this, `panelContact()` is the
single place to change. Tracking IDs are allocated within 0..65535 without colliding with held contacts. Twelve
algorithm slots are supported in software; this is not a physical finger-count
claim. Pressure is only a binary contact indicator, not measured force.

The private calibration file can override the panel default with a top-level
`"invert_y": true` (reverse processor Y) or `"invert_y": false` (report processor Y
directly). Omitting the field keeps the existing CSOT/BOE default. X, tracking and
electrode calibration are unchanged. Restart `piano-thp` after changing the file.
Use this override or a libinput Y correction, not both.

SIGINT/SIGTERM stop the process, release contacts and close the stream. Framing
or I/O failure terminates it and removes the virtual input device. No retry
loop conceals a failed controller. Kernel shared-power/suspend limitations
remain; this is not yet a default product service.

An optional `--record /path/to/private/capture.ntp1` saves exactly the bytes read,
including invalid or partially received records. The file is created exclusively
with mode 0600; existing files are not overwritten. Recording is off by default
and continues until exit, so use a bounded diagnostic session and keep it private.

## Focused Tests

`test-live CALIBRATION.json STOCK_RECORDS.bin` can additionally wrap the ten
user-supplied Android idle payloads in synthetic NTP1 envelopes. This tests the
reader/processor boundary, not real kernel wire output or gestures.

`make build/test-uinput` builds a separate test requiring Linux uinput/evdev,
devtmpfs and sysfs. It is deliberately excluded from `make test`: run
`build/test-uinput --create-test-device` only in a disposable guest because it
creates synthetic input. It checks all twelve slots, coordinates, partial/full
release, tracking-ID wrap and device removal through the real kernel interfaces.

## Read-only frame metadata

`tools/thp-frame-fields.py` reads an existing regular NTP1 `--record` capture.
It emits JSON lines when the vendor metadata changes, with the original
CLOCK_BOOTTIME timestamp and sequence/epoch/loss indicators:

```sh
python3 tools/thp-frame-fields.py /path/to/private/capture.ntp1
python3 tools/thp-frame-fields.py /path/to/private/growing.ntp1 --follow-seconds 180
```

Offsets are relative to the THP payload after the 32-byte NTP1 header and
257-byte point prefix: `+54` frequency index (u8), `+32/34/36/38` noise (four
little-endian u16 values), `+52` scan rate (little-endian u16), and `+24` mode
(u8). They follow the MiCode vendor parser at commit
`6957f6b646d1c919e175e6f9000eb50c8635273c`,
`p81/nt36532/nt36xxx.c:nvt_ts_prase_data_func`, around line 2819.
Values are reported without interpreting frequency indices as frequencies or
sentinel noise values as physical units. Invalid frames produce an explicit
invalid record without metadata; unchanged valid fields are suppressed.

The tool does not open the exclusive touch device, create input events, change
controller commands or modify a service. Use it only when a recording already
exists or a separately authorized diagnostic producer has recording enabled.
It does not enable recording in the product service. Keep recordings and
results private; CLOCK_BOOTTIME includes sleep, unlike dmesg's monotonic clock.
