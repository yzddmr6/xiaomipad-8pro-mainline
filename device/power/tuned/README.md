# Piano power profiles

MIT-licensed board profiles for Ubuntu TuneD 2.25.1. Select Power Saver,
Balanced or Performance in GNOME's quick settings or Settings → Power.
Balanced is the default. All three profiles use schedutil.

| PPD profile | TuneD profile | policy0 min / max (kHz) | policy6 min / max (kHz) | CPU boost | GPU min / max (Hz) |
|---|---|---:|---:|---:|---:|
| power-saver | piano-power-saver | 384000 / 1785600 | 1017600 / 1958400 | 0 | 160000000 / 342000000 |
| balanced | piano-balanced | 1785600 / 3532800 | 1689600 / 4320000 | 1 | 160000000 / 1100000000 |
| performance | piano-performance | 2918400 / 3532800 | 2841600 / 4320000 | 1 | 160000000 / 1100000000 |

- **Balanced** caps no frequency; the prime cores' 4204800 and 4320000 kHz
  boost points stay enabled, although the governor currently asks for at most
  4089600 kHz. A busy core does not run below about 1.7 GHz, so scrolling
  and animations do not wait for the governor to ramp up from the lowest
  points; idle cores still sleep.
- **Performance** keeps the same range and raises the lowest frequency a busy
  core runs at to about 2.9 GHz. Short bursts (opening an app, scrolling, the
  thread that feeds the GPU) then start fast instead of waiting for the
  governor to ramp up. Idle cores still sleep.
- **Power Saver** caps the CPU and GPU, and caps utilization floors at 320
  (`kernel.sched_util_clamp_min`), which is what the six smaller cores deliver
  at their capped frequency. A larger floor, such as the one a virtual machine
  asks for, would otherwise count as fitting no capped core and be crowded
  onto the two prime cores.

In every mode, real-time threads (audio, the compositor) get no utilization
floor by default (`/usr/lib/sysctl.d/50-piano-uclamp.conf`).

## Package ownership

`piano-ubuntu-desktop` depends on Ubuntu `tuned-ppd`, which provides the
`power-profiles-daemon` dependency and D-Bus API and conflicts with that daemon.
Profile sources are installed once under `/usr/lib/tuned/profiles/piano-*`;
administrator overrides belong under `/etc/tuned/profiles/`.

Ubuntu `tuned-ppd` already owns `/etc/tuned/ppd.conf` as a conffile. Sharing it
between packages would fail dpkg unpack; diverting a conffile is unsuitable.
Our package therefore installs `ppd.conf` as **`/etc/tuned/piano-ppd.conf`**, a
separate dpkg conffile that preserves administrator changes during upgrades.
A standard `tuned-ppd.service` drop-in uses `BindReadOnlyPaths` to present it as
`/etc/tuned/ppd.conf` inside that service. The host's Ubuntu conffile is untouched;
there is no wrapper daemon, rewritten Python module or configuration copier.
Reboot after installing/upgrading the desktop package to activate the mount.
Removing the desktop package removes the drop-in; after a reboot the service
uses Ubuntu's original configuration. Purging also removes the piano conffile.

Sources: [Ubuntu package](https://packages.ubuntu.com/resolute/tuned-ppd),
[file list](https://packages.ubuntu.com/resolute/all/tuned-ppd/filelist),
[TuneD constants](https://github.com/redhat-performance/tuned/blob/v2.25.1/tuned/consts.py),
[systemd mounts](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#BindPaths=).

## Command line

This package deliberately does not provide `powerprofilesctl`. Ubuntu 26.04's
[contents index](https://packages.ubuntu.com/search?searchon=contents&keywords=powerprofilesctl&mode=filename&suite=resolute&arch=arm64)
ships it only in `power-profiles-daemon`, which cannot coexist with `tuned-ppd`.
GNOME uses D-Bus directly and does not need that CLI. Keeping a separate copied
GPL script, its licence/source and version maintenance adds no required feature;
use the distribution's `tuned-adm` instead:

```sh
tuned-adm active
sudo tuned-adm profile piano-balanced   # or piano-power-saver / piano-performance
sudo tuned-adm verify
```

For an installation that previously copied the official CLI manually to
`/usr/local/bin/powerprofilesctl`, remove that known manual copy during migration
(`sudo rm /usr/local/bin/powerprofilesctl`). Packages neither install files in
`/usr/local` nor silently delete administrator-managed files there.
