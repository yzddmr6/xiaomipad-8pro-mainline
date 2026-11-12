# Release history and current support

Current release: **v1.2.1**. See the [README](../README.en.md#hardware-support) or
[中文 README](../README.md#硬件支持) for the hardware support tables and usage instructions.

## Current support

Ubuntu 26.04 GNOME runs natively with Android dual boot. Supported features include
GPU-accelerated desktop and snap applications, 3200×2136 display with 60/90/120/144 Hz
selection, Night Light, touch input, magnetic keyboard, Wi-Fi, Bluetooth pairing and
audio, speaker playback, microphone recording, front and rear cameras, sensors,
auto-rotation, 5 Gbit/s USB host / OTG with docks, USB storage, keyboards and mice,
USB-C external displays (DisplayPort Alt Mode, tested at 4K 60 Hz as an extended
desktop), charging and system suspend. The power button locks the screen by default and
GNOME offers Power Saver, Balanced and Performance modes. Optional command-line virtual
machines run Ubuntu and Windows 11 ARM guests; see the [VM guide](VIRTUALIZATION.md).
Waydroid runs Android apps with software rendering.

The system player (GStreamer), Firefox (H.264/HEVC/VP9) and Chrome (H.264/VP9) use
hardware decoding automatically; AV1 uses software decoding. H.264/H.265 hardware
encoding is verified at 1080p and 4K. Multi-finger touch and volume keys work. The original adapter supports PPS fast
charging, with current adjusted to charge level and temperature; Preserve Battery Health continues to stop charging at 80% and resume at 75%. Keep the default
speaker volume cap because speaker protection is absent. System time is set from the network after boot.
Sensor calibration defaults are included; see the
[calibration instructions](../device/sensors/README.zh-CN.md) to use your own tablet's calibration.

Not yet supported: stylus, fingerprint, disk hibernation and hardware clock writes.

## Test configuration

Results are from one Xiaomi Pad 8 Pro (`piano`), model 25091RP04C (China), with
Snapdragon SM8750P, 16 GB RAM, 512 GB UFS and an unlocked bootloader. The stock build
is OS3.0.303.0.WPYCNXM on slot A. Software is Linux 7.2.15 with the piano patches,
Ubuntu 26.04 Desktop ARM64 and Mesa 26.2 for desktop and snap applications; sensors
use the default calibration. The kernel input is pinned in
[kernel/source.json](../kernel/source.json). The v0.2.0 fresh installation was
verified on this unit; later versions were tested on the installed system. These
results do not establish support for other models, capacities or accessories.

## Completed work by release

### v1.2.1

- Host Mesa uses standard Ubuntu library paths, allowing sandboxed web processes to use Adreno 830 acceleration.
- Vulkan lists the Adreno 830 once instead of twice.
- Settings is updated to 50.3, retaining the power-button lock option.
- Complete release batches support upgrading and rolling back userspace packages together with the boot set.
- GunyahVM 1.1.0 keeps accelerated Linux guests working with the updated graphics packages.

### v1.2.0

A guarded overlay correction restores web pages and shared storage in Waydroid images that declare an absent OMX service.

The default packages remove the legacy USB debug console; normal maintenance uses developer-mode SSH.

Preinstalled GunyahVM graphical virtual-machine management.

### v1.1.1

- PPS fast charging with the original adapter adjusts current to charge level and temperature, while keeping Preserve Battery Health.
- Suspending right after turning the screen on no longer wakes the tablet immediately through the keyboard.
- Ambient-light readings no longer stop after the sensor is switched off and on.
- Rear torch control, with a check tool that verifies readback and restores the previous setting.
- Mesa 26.2.6, with upstream synchronization, rendering and concurrency fixes.
- A desktop notification appears when a new release is available; it can be turned off in settings.
- Migrating from the Firefox snap keeps existing user data.
- [Documentation and an offline preparation tool](RESTORE-STOCK.en.md) for restoring HyperOS from a dual-boot installation; the tool checks the tablet’s own GPT backups and does not flash the device.

### v1.1.0

- Preserve Battery Health in Settings → Power stops charging at 80% and resumes at 75%; while plugged in below the limit the tablet stays awake so charging stops on time.
- Closing the keyboard cover on external power locks the screen without suspending.
- If Ubuntu is unresponsive, holding both volume keys for 10 seconds with the cover open switches back to Android.
- `piano-check` reports system and hardware status; `piano-check --markdown` gives an issue-ready summary.
- Waydroid runs Android apps with software rendering; see the [Waydroid guide](WAYDROID.en.md).
- USB recovers after a USB-C display is unplugged, without a restart.
- Linux 7.2.15, with the Ubuntu generic kernel's USB and Bluetooth peripheral drivers.

### v1.0.0

- Windows 11 ARM virtual machines can be created from your own official ISO and used through a local VNC viewer; the firmware, test-signed drivers and media tools are an optional install.
- Regular users can create and manage ARM64 Ubuntu guests with SSH, NAT networking, persistent disks and restart after a clean shutdown.
- The VM tools are installed separately and the virtualization modules load only when needed; device and network actions each ask for administrator authorization.
- The [VM guide](VIRTUALIZATION.md) covers installation, creation, use and removal.
- The desktop and applications load the Adreno 830 Mesa build through the system library path instead of environment variables; Settings → System → About → System Details shows the correct GPU, and graphical programs started with sudo are hardware accelerated too.

### v0.10.0

- Camera auto exposure no longer flickers around bright areas and returns to normal brightness within about 1–3 seconds after the lens is covered or pointed at a light; the rear camera gain has finer steps.
- Both cameras report their real frame size and frame rate to applications, and incomplete frames are no longer passed on.
- Hardware video decoding finishes the old picture sequence correctly when the resolution changes; H.264/H.265 hardware encoding produces key frames on request.
- Error handling and resource cleanup are corrected when the USB-C display link fails to start.
- Charging reaches 100% at normal temperatures, with a lower termination voltage from 48 °C.
- The app center and other apps using Flutter's new renderer display text again.
- Wireless statistics no longer time out repeatedly after the router changes its channel or bandwidth.

### v0.9.2

- Fixes right-half horizontal lines, tearing and display corruption on affected BOE panels; confirmed by affected users after upgrading.
- Fixes a duplicate resource release during display driver initialization and improves cleanup when power or clock operations fail.
- Touchscreen Y orientation can be configured per device without changing the defaults on other tablets.
- Settings → System → About includes the author and a link to the project homepage.

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

- GPU compute performance improves by about 24% in Geekbench 6 Vulkan, with some workloads nearly doubling; the kernel is unchanged from v0.7.0.

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
- The rear camera has no autofocus; nearby subjects may be out of focus.

Installation and maintenance instructions are in [INSTALL.en.md](INSTALL.en.md),
[UPGRADE.en.md](UPGRADE.en.md) and [FLASHING.en.md](FLASHING.en.md).

### v1.3.0

⟦DRAFT⟧

Linux 7.4-rc2、NPU 离线演示、GNOME 硬件录屏、Mesa 驱动修复与系统切换保护；GunyahVM 1.2.0 支持 Linux 和 Windows 来宾。

Linux 7.4-rc2, an offline NPU demo, hardware screen recording in GNOME, Mesa driver fixes and system-switch safeguards. GunyahVM 1.2.0 supports Linux and Windows guests.
