# Upgrade

## System updates

Open Software Updater, or run `sudo apt update && sudo apt full-upgrade`. Restart after a kernel update.

## First-time setup

On an existing installation, run these commands once on the tablet, then restart.

```sh
curl -fLO https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/download/apt/piano-archive-keyring_1_all.deb
sudo apt install ./piano-archive-keyring_1_all.deb
sudo apt update && sudo apt full-upgrade
```

## Recovery

If the system fails to boot, reinstall using the [installation guide](INSTALL.en.md).
