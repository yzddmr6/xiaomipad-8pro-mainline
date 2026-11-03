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

- Command-line guests only; no graphical desktop, GPU, audio or USB passthrough.
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
