# Contributing

[中文](CONTRIBUTING.md)

## Where to send changes

- Integration, device userspace, packaging, installer tools and documentation: this repository, [xiaomipad-8pro-mainline](https://github.com/yzddmr6/xiaomipad-8pro-mainline), targeting `main`.
- Kernel drivers, device trees and kernel configuration: [linux-sm8750-piano](https://github.com/yzddmr6/linux-sm8750-piano), targeting `piano/dev`.
- When both need changes, open one PR in each, link them to each other and state the merge order.
- Open an issue first before changing release-related files or project policy.

## Checklist before you open a PR

Each item must be visible as a fact in the PR; where an item does not apply, write "N/A" and why. Documentation-only changes need documentation checks only. Tools are verified by what they affect: a change to the build configuration, image contents, installation or recovery is verified on the resulting artefact and device boundary, even though the tool runs on the host; paths that reliable offline evidence covers need no repeated destructive run.

1. **One topic per PR.** One feature or one fix that can be reviewed and reverted on its own. Pen, camera flash and fingerprint each get their own PR; keep refactoring and formatting apart from features. Drop commits that are already merged, superseded or unrelated. Each commit is one logical change that builds on its own; its message explains the problem and why the change is needed.
2. **Based on the latest target branch.** Your `origin` is usually your own fork: add this project's repository (`git remote add upstream https://github.com/yzddmr6/xiaomipad-8pro-mainline.git`), `git fetch upstream`, rebase onto the latest `upstream/main` and resolve conflicts yourself. Review the complete final diff (`git diff upstream/main...HEAD`); for files that had conflicts, compare the content before and after the replay to confirm nothing was dropped. `--stat` only shows the scope.
3. **Claims match the facts.** Statements about features and fixes in the title, body and `Fixes:` must match the final diff and the real call path; a bug fix comes with a reproduction on the current target branch or a source reference. Test statements name the tested build, scenario and result; after a rebase or code change, say which earlier evidence still applies and what was re-tested.
4. **Build the way releases are built.** Use the entry points in the [build guide](docs/BUILD.en.md) with the complete release configuration (every kernel configuration fragment listed in `kernel/source.json`, matching kernel modules and DTB, packages and services), and name the commit or artefact you tested. Interfaces, sysfs nodes and services you call must exist in the tested combination: the target branch, this PR, or a linked companion PR; name the provider and the merge order, and verify the behaviour when an optional provider is absent. Do not code against an assumed interface; a successful compile does not mean it works.
5. **Hardware parameters have a source.** Fixed memory addresses, register values, timings, voltages and currents need a stock or upstream reference and must hold for every supported variant: both panels (CSOT and BOE), each memory and storage size, with and without each peripheral. When you change shared code, state its effect on existing users.
6. **Test on the device.** With the change installed on your device (say whether RAM boot or installed system), confirm at least:
   - a cold boot reaches the login screen or desktop;
   - compared with the build before your change, `dmesg` shows no new `Oops`, `BUG`, `WARNING`, `hung task`, `soft lockup` or `Call trace`;
   - suspend and resume work, with sleep time checked against the wall clock (`tools/piano-suspend-check.py`); if the change touches power or charging, also test on battery;
   - the display and touch lifecycle: screen off and on, lock and unlock, wake after a long screen-off;
   - the changed subsystem works for real; when a new optional feature is absent or fails, unrelated existing functions (boot, touch, suspend) keep working, and a failing required dependency reports the error and releases its resources. Check error paths by source review or offline fault injection rather than unloading or rebinding drivers of hardware in use.

   Keep a new feature you are not sure about disabled by default; add no periodic polling, retries, delays or wakeups without protocol evidence.
7. **Attribution and sources.** Commit under your own identity (the author wrote the change, the committer applied it). For imported code, keep the original author, date and existing trailers, give an accessible source link, put local changes in separate commits, and record the component, source, licence and local changes in [NOTICE](NOTICE) (SPDX/REUSE). Stock files follow the [firmware policy](firmware/README.md). If you used AI, add a `Co-Authored-By:` trailer to each affected commit naming the model you actually used, for example `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, one per model, and name the tools, models and what they were used for in the PR; no prompts or session logs. Merged commits are not rewritten to add this. An AI must not add anyone's `Signed-off-by:` or `Tested-by:`, or claim tests that did not happen.
8. **Stay within your role.** An ordinary PR does not allocate kernel ABI numbers, edit `kernel/abi.json` or release version records, widen permissions or write scope, or announce approvals, authorisations, test conclusions or releases on a maintainer's behalf. When a source, patch or dependency directly related to the topic changes, update its hashes with it and state the source and re-verification; never change an expected value just to make a check pass. Maintainers allocate ABIs when packaging any installable kernel ([release documentation](docs/RELEASE.md#installable-kernel-packaging-maintainers)) and prepare release notes.
9. **No personal device data.** Commits, generated files and default log output contain no serial numbers, Bluetooth or Wi-Fi addresses, per-unit calibration, credentials, backups, personal paths or session logs. Sanitise logs before posting them.

## Design conventions

- Prefer distribution packages. When a capability is missing, pin the upstream source, carry only minimal patches and state when they can be removed. Reuse upstream build systems.
- Hardware protocols and device userspace use standard Linux subsystem interfaces and do not depend on a particular distribution, package manager, desktop or fixed account. Packaging follows [Debian Policy](https://www.debian.org/doc/debian-policy/); service units and desktop policy stay separate from hardware processing.
- Build and packaging tools never touch a device implicitly; inputs are pinned and publicly obtainable, with no private paths, outputs of another workspace or another unit's calibration.

<a id="evidence"></a>
## What a PR or issue report contains

Fill in the [PR template](.github/pull_request_template.md): problem and change; topic, target branch and base commit, dependencies and merge order; sources and licensing; AI assistance (tools, models and what for); validation — model and panel, memory and storage, stock build, kernel release and commit, integration commit or image (the output of `piano-check --markdown` covers most of this), how it was tested (offline, RAM boot, installed cold boot, upgrade), commands and results, sanitised logs; what remains untested or limited.

Compilation, QEMU, a successful probe or an active service does not show that hardware works; a result on another panel or another unit does not show that this one works. Current support is in [STATUS](docs/STATUS.md) and the [hardware support table](README.en.md).

## How PRs are merged

Maintainers review open PRs once per release. Mergeable PRs are merged as they are; for small problems a maintainer may merge and fix, split, or take only part of a PR, and will say so in the PR. The combination a maintainer integrates is checked against the same list.

Update the Chinese and English documents together. Commands in the documentation are not permission to flash, wipe or reboot anyone else's device.
