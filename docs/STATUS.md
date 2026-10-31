# Release history and current support

Current release: **v0.9.1**. See the [README](../README.md#hardware-support) or
[中文 README](../README.zh-CN.md#硬件支持) for the hardware support tables and usage instructions.

## Current support

Ubuntu 26.04 GNOME runs natively with Android dual boot. Supported features include
GPU-accelerated desktop and snap applications, 3200×2136 display with 60/90/120/144 Hz
selection, Night Light, touch input, magnetic keyboard, Wi-Fi, Bluetooth pairing and
audio, speaker playback, microphone recording, front and rear cameras, sensors,
auto-rotation, 5 Gbit/s USB host / OTG with docks, USB storage, keyboards and mice,
USB-C external displays (DisplayPort Alt Mode, tested at 4K 60 Hz as an extended
desktop), charging and system suspend. The power button locks the screen by default and
GNOME offers Power Saver, Balanced and Performance modes.

The system player (GStreamer), Firefox (H.264/HEVC/VP9) and Chrome (H.264/VP9) use
hardware decoding automatically; AV1 uses software decoding. H.264/H.265 hardware
encoding is verified at 1080p and 4K. Multi-finger touch, volume keys and fast charging
(USB PD 9 V, PPS direct charging, Xiaomi charger authentication) work. Keep the default
speaker volume cap because speaker protection is absent. System time is set from the network after boot.
Sensor calibration defaults are included; see the
[calibration instructions](../device/sensors/README.zh-CN.md) to use your own tablet's calibration.

Not yet supported: stylus, fingerprint, disk hibernation and hardware clock writes.

## Test configuration

Results are from one Xiaomi Pad 8 Pro (`piano`), model 25091RP04C (China), with
Snapdragon SM8750P, 16 GB RAM, 512 GB UFS and an unlocked bootloader. The stock build
is OS3.0.303.0.WPYCNXM on slot A. Software is Linux 7.2.10 with the piano patches,
Ubuntu 26.04 Desktop ARM64 and Mesa 26.2 for desktop and snap applications; sensors
use the default calibration. The kernel input is pinned in
[kernel/source.json](../kernel/source.json). The v0.2.0 fresh installation was
verified on this unit; later versions were tested on the installed system. These
results do not establish support for other models, capacities or accessories.

## Completed work by release

### v0.9.1

- Both display links keep their clock running while the screen is on, instead of stopping and restarting it about 174 times per frame during blanking; the right-half corruption seen on some tablets after a fresh install is related to the second link.

### v0.9.0

- The second display link, which drives the right half of the screen, is brought up as the stock software does; the right-half corruption seen on some tablets after a fresh install is related to this link.
- The kernel is updated to Linux 7.2.14 with 619 upstream stable fixes.
- The Xiaomi keyboard with a touchpad has its touchpad detected automatically; keyboards without a touchpad are unaffected.

### v0.8.0

- In system sleep the whole chip now reaches a deep power-saving state, with the CX logic supply off and memory in self-refresh.
- The hardware clock keeps the correct time across reboots and switches to Android after the first network time sync.
- Image processing compute hotspots on the GPU are about 27–29% faster.
- The display defaults to 120 Hz, and on battery the tablet sleeps after five idle minutes.

### v0.7.1

- GPU compute performance improves by about 24% in Geekbench 6 Vulkan, with some workloads nearly doubling; the kernel remains piano40.

### v0.7.0

- 11 more on-board temperature readings are available, and the task manager has a Temperature page listing every sensor with history graphs.
- Internal storage uses UFS multi-circular queues, one request queue and interrupt per CPU core.

### v0.6.2

- The task manager shows CPU temperature, the GPU model, frequency, temperature and utilisation, per-process GPU usage and each battery's temperature.
- Sound, sensors and USB-C come up on every boot; an occasional boot without them is fixed.

### v0.6.1

- The occasional single-frame whole-screen flash while touching the screen is fixed; touch response is steadier.
- The panel type (CSOT or BOE) is detected automatically and loads its own touch firmware, touch orientation and display initialization.
- The sensor service keeps the sensor registry updates written by the DSP.
- A sensor registry that keeps crashing the ADSP (no sound, no sensor data) is moved aside at boot and the default state is restored.
- The installation guide covers the partition layout of the 256 GB model.

### v0.6.0

- USB host and device modes run at 5 Gbit/s SuperSpeed in both Type-C orientations.
- USB-C external displays (DisplayPort Alt Mode) work in both orientations and with hot-plug; tested at 4K 60 Hz as an extended desktop.
- The system player (GStreamer), Firefox (H.264/HEVC/VP9) and Chrome (H.264/VP9) use hardware video decoding automatically.
- The maximum speaker volume has been increased.
- The default filesystem character set is UTF-8, including Chinese filenames on USB storage.

### v0.5.1

- Touch recovers reliably after the touch service restarts.
- Repeated quick logout and login cycles no longer stall the system.

### v0.5.0

- USB host / OTG for docks, USB storage, keyboards and mice, including charging while using a powered dock.
- The power button locks the screen by default and wakes the lock screen when pressed again.
- Power Saver, Balanced and Performance modes in GNOME, with Balanced as the default.
- Showtime and GStreamer playback dependencies installed by default, with software decoding fallback.
- The touch service retries with increasing delays instead of giving up when its stream cannot start.

### v0.4.0

- Touch works after switching to 120 or 60 Hz and reliably returns after screen on.
- Screen wake is about 0.3 seconds faster.
- Includes the camera and video display improvements from v0.3.2.

### v0.3.2

- Removed diagonal stripes in camera previews and video frames in GTK applications.
- Reduced CPU use during camera preview while retaining the optional BGRx output.

### v0.3.1

- Removed the brief garbled frame when turning the screen off, locking or suspending.
- Both cameras become available automatically in camera applications, with a working Snapshot preview.
- Touch recovers automatically after a touch service stream error.
- The power-button action is configurable in Settings → Power, with suspend as the default.

### v0.3.0

- Added GNOME Night Light and front/rear camera capture with fixed rear focus.
- Enabled system suspend with timer, power-button, keyboard and cover-open wake.
- Added keyboard wake with the screen off and from suspend.
- Added H.264/H.265/VP9 hardware decoding and H.264/H.265 encoding at 1080p and 4K.
- Improved refresh-rate switching, screen off/on, charging restoration after suspend,
  microphone recording level and desktop/snap graphics compatibility.
- Supported upgrades from v0.2.0 without repartitioning or reflashing the root image.

### v0.2.0

- Delivered the first fully verified fresh installation and Android/Ubuntu switching
  from Ubuntu, Android's KernelSU module and host Fastboot.
- Added native 3200×2136 display at 144 Hz, adjustable brightness, screen off/on and
  touch recovery after screen on.
- Enabled Adreno 830 acceleration for the desktop and snap applications, including Firefox,
  and verified 1080p H.264 hardware decoding.
- Delivered Wi-Fi, microphone recording, speaker playback with capped volume,
  magnetic keyboard input, sensors and auto-rotation with the keyboard detached.
- Added battery and temperature reporting, installation assistance and upgrade documentation.

## Usage notes

- Some HEVC videos may drop a small number of frames in Firefox.
- After an OTG device is unplugged, a plain OTG adapter plugged in next may not be detected; plug a charger or computer cable in and out once, or use a dock.
- Bluetooth occasionally needs reconnecting before data or audio works again.
- The rear camera has fixed focus; nearby subjects may be blurry and color tuning is limited.

Installation and maintenance instructions are in [INSTALL.md](INSTALL.md),
[UPGRADE.md](UPGRADE.md) and [FLASHING.md](FLASHING.md).
