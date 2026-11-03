# Command-line virtual machines

On an Ubuntu installation with virtualization support, create an Ubuntu 26.04 ARM64 guest with SSH, NAT networking and a persistent disk. Defaults are 2 vCPUs, 2 GiB RAM and an 8 GiB sparse disk.

## Enable

Update the kernel and device support package following the latest release instructions, then enter `optional/virtualization/` in its upgrade archive and install the optional packages from that same release. Use a terminal in the tablet's local desktop session:

```sh
sudo apt install --no-install-recommends ./piano-crosvm_*_arm64.deb ./piano-vm_*_arm64.deb
sudo usermod -aG gunyah "$USER"
```

Log out and back in to acquire the group membership and memory limit. If the memory-lock limit is still insufficient, reboot the host and log in again. Grant access only to trusted accounts. The VM runs as your user; enabling the device and managing its network require separate administrator authentication.

Use an existing SSH public key. If you do not have one, run `ssh-keygen -t ed25519` and choose a destination at its prompt. Private keys are never copied into the guest.

## Create and use

After each host boot, enable virtualization before the first VM:

```sh
piano-vm load
piano-vm create ubuntu --start
piano-vm list
piano-vm console ubuntu
```

The initial creation downloads an Ubuntu cloud image. Ctrl-C exits the log viewer without stopping the VM. Once list reports guest_ready and an IP address:

```sh
piano-vm ssh ubuntu
piano-vm ssh ubuntu -- 'sudo apt-get update'
piano-vm stop ubuntu
piano-vm start ubuntu
```

Creation options include `--username vmuser --cpus 2 --memory-mib 2048 --disk-gib 16 --ssh-key ~/.ssh/id_ed25519.pub`. Data defaults to `~/.local/share/piano-vm/`. Sparse disks grow with writes; keep sufficient free host storage. Data survives shutdown.

After a guest kernel update, shut down the guest and run start again. The tool extracts a matching kernel and initrd from that disk and retains the previous boot files. Rebooting inside the guest does not refresh host boot files.

stop requests a clean guest shutdown, waits for exit and removes its network. Run stop after a guest shuts itself down too, to remove the remaining network. Use `piano-vm stop ubuntu --force` only for an unresponsive guest when you accept potential loss of unwritten data. Logs and exit results are under runs/ and state.json in the VM directory.

## Limits

- Ubuntu guests provide a command line; no Ubuntu graphical desktop, GPU, audio or USB passthrough.
- One running VM per user. Keep guest RAM at or below 3 GiB. Guest memory is unavailable to the host while running; insufficient limits or available RAM cause an explicit error.
- Manage network authentication from the tablet's local desktop terminal. Keep the host awake while a VM runs, and stop guests before host shutdown or reboot.
- Userspace virtio-net is the default. Optional vhost acceleration requires separate administrator-managed device permissions.
- SSH uses public keys; the default account has no login password. The serial console mainly displays boot progress. Sandbox failures never trigger an automatic downgrade; retain the error log.

## Delete and uninstall

```sh
piano-vm stop ubuntu
piano-vm delete ubuntu
```

Deletion permanently removes that VM's disk, configuration and logs. Back up needed data first. After stopping all VMs and removing their networks:

```sh
sudo apt purge piano-vm piano-crosvm
sudo gpasswd -d "$USER" gunyah
```

Keep the device support package needed by the tablet. Removing packages does not delete VM data in home directories. Reboot to clear modules and the large-page memory policy enabled for this boot; do not unload active drivers manually.

## Windows 11 ARM

Supply your own official Microsoft ARM64 ISO containing `sources/install.wim` and an applicable Windows licence. The tool does not provide Windows, a product key or a preset account. Keep at least 40 GiB free on the ext4 or XFS filesystem containing your home directory. The system disk is a 64 GiB sparse file; installation and updates increase its physical usage.

From `optional/virtualization/` in the matching upgrade archive, install the additional Windows package:

```sh
sudo apt install --no-install-recommends ./piano-crosvm_*_arm64.deb ./piano-vm_*_arm64.deb ./piano-vm-windows_*_arm64.deb
```

This optional package supplies the preparation tools, firmware, drivers and Remmina VNC client. Linux users do not need it. After updating device support, log out and back in to acquire the gunyah group's 6 GiB memory-lock limit; reboot and log in if the limit is still insufficient.

```sh
piano-vm load
piano-vm create windows --os windows11 --iso ~/Downloads/Windows11_ARM64.iso --start
piano-vm view windows
```

Creation lists the editions in the WIM and asks you to select one. Alternatively, pass `--edition 'Windows 11 Pro'`; the name must occur in that ISO. Defaults are 4 vCPUs, 4608 MiB RAM and a 256 MiB DMA pool. RAM is limited to 5120 MiB. Insufficient memory-lock limits or available host RAM cause an explicit refusal. Linux defaults remain unchanged.

Media preparation runs entirely as your user, without mounting the ISO or virtual disks. The first boot runs DISM inside WinPE and shows deployment progress. After successful deployment and clean shutdown, the tool detaches and deletes the installation media, then boots the system disk into OOBE. Failed deployments retain media and logs for inspection. A force stop does not mark deployment complete.

Complete ordinary OOBE in the Remmina window. Initial OOBE downloads updates; a normal Windows-requested reset starts the VM again. If the viewer disconnects, run `piano-vm view windows` again. VNC listens only on `127.0.0.1`, without a LAN listener. Use `--vnc-port 5901` at creation if another local port is needed.

No local account, automatic login or RDP is enabled by default. To create an explicit local account, add `--local-user vmuser` and enter its password interactively. The answer file uses Windows' hidden-password encoding, **not encryption**. Host installation media is removed after deployment; guest answer files are removed after first login. Enable RDP yourself in a Windows edition that supports it, and confirm its server certificate in your client.

Shut down using Windows' Start menu, then clean up the network. To start again:

```sh
piano-vm stop windows
piano-vm start windows
piano-vm view windows
```

While Windows is running, normal stop asks you to shut down inside the guest. `stop --force` is an abrupt stop that can lose unwritten data. Close the guest before host shutdown or reboot, and keep the host awake while it runs.

Limits: the display is a 1280×720 software framebuffer without GPU acceleration. There is no vTPM; guest Secure Boot is off and TPM and other installation checks are bypassed. The test-signed drivers produce a Windows Test Mode watermark; keep their required test-signing settings enabled. Hibernation and Fast Startup are disabled. Audio and USB passthrough are not provided.

After shutdown, `piano-vm delete windows` deletes that VM. Remove just the Windows tools with `sudo apt purge piano-vm-windows` while retaining the Linux VM tools. Package removal does not replace VM data deletion.
