# Piano power profiles

MIT-licensed board profiles for Ubuntu TuneD 2.25.1. Select Power Saver,
Balanced or Performance in GNOME's quick settings or Settings → Power.
Balanced is the default. All three profiles use schedutil, preserve CPU
minimum frequencies and keep the GPU minimum at 160 MHz.

| PPD profile | TuneD profile | policy0 maximum (kHz) | policy6 maximum (kHz) | CPU boost | GPU minimum / maximum (Hz) |
|---|---|---:|---:|---:|---:|
| power-saver | piano-power-saver | 1785600 | 1958400 | 0 | 160000000 / 342000000 |
| balanced | piano-balanced | 3532800 | 4089600 | 0 | 160000000 / 1100000000 |
| performance | piano-performance | 3532800 | 4320000 | 1 | 160000000 / 1100000000 |


These limits and GNOME switching have been checked on the device. The v0.5.0
packaged profiles and service configuration have also been checked:
all three modes match their CPU/GPU limits, and Power Saver persists across a
cold boot. Performance permits CPU boost; it does not promise sustained
all-core performance.

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
