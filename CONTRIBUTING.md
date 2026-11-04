# Contributing

## Where to send changes

Send integration, device userspace, packaging and documentation issues and pull requests to
[xiaomipad-8pro-mainline](https://github.com/yzddmr6/xiaomipad-8pro-mainline), targeting `main`.
Send kernel drivers, DTS and configuration to
[linux-sm8750-piano](https://github.com/yzddmr6/linux-sm8750-piano), targeting `piano/dev`.
Follow the execution boundaries in [AGENTS.md](AGENTS.md).

Open an issue before proposing changes to release-related files or policy. Maintainers allocate ABI
numbers when packaging any installable kernel, including candidates; ordinary PRs do not reserve
numbers or edit `kernel/abi.json` or release locks. See
[installable kernel packaging](docs/RELEASE.md#installable-kernel-packaging-maintainers).
Maintainers prepare release notes through
[release preparation](docs/RELEASE.md#release-preparation-maintainers).
Update English and Chinese counterparts together.

## Commits and attribution

Use [Git's author and committer semantics](https://git-scm.com/docs/git-commit#_commit_information):
the author is the person who wrote the change; the committer is the person applying it.
Imported commits retain their original author, date and existing trailers. Give an accessible
upstream source link and identify local changes separately.

In this integration repository, disclose AI assistance with a `Co-Authored-By:` trailer.
An assistant must not add anyone's `Signed-off-by:` or `Tested-by:`; those attestations come from
the person concerned. This does not prohibit factual `Fixes:`, `Link:` or `Reported-by:` trailers:
use them as described in the kernel's
[submitting-patches](https://docs.kernel.org/process/submitting-patches.html).

Keep each commit to one logical change and buildable on its own. Explain the problem and why the
change is needed. For kernel contributions, follow `Documentation/process/` in the kernel tree:
[submitting-patches](https://docs.kernel.org/process/submitting-patches.html),
[coding-assistants](https://docs.kernel.org/process/coding-assistants.html) and
[generated-content](https://docs.kernel.org/process/generated-content.html).
The human kernel contributor reviews the submission and supplies their own DCO sign-off;
the upstream documents govern tool attribution there.

## Evidence

A hardware report includes:

- Device model and panel variant, stock build, kernel release and commit, and integration commit
  or image identity.
- Reproduction steps, expected and observed results, and sanitized logs.
- What was tested, what remains untested, and whether this was an offline check, a RAM boot,
  an installed-system cold boot or an upgrade.

Apply the [privacy boundary](AGENTS.md) before attaching evidence. Compilation, QEMU, a probe or
an active service does not establish hardware support. Results from another variant do not
establish support for this one. Current tested behavior belongs in
[STATUS.md](docs/STATUS.md) and the [support matrix](README.en.md#hardware-support).

Optional hardware must not disrupt boot, touch or suspend when absent or failing. Do not add
periodic polling or wakeups without protocol evidence and device tests. Keep an unverified
feature disabled by default.

## Sources and integration

Preserve upstream authorship and per-file licenses. Record imported components, their pinned
sources, licenses and local changes in [NOTICE](NOTICE); use
[SPDX identifiers and REUSE metadata](https://reuse.software/spec-3.3/).
Stock-file inclusion is governed by the [firmware policy](firmware/README.md).

Prefer distribution packages. Where a required capability is missing, pin upstream sources and
minimal patches and state when the override can be removed. Reuse upstream build systems.
Keep package recipes, service units and desktop policy separate from hardware processing;
use [Debian Policy](https://www.debian.org/doc/debian-policy/) for Debian packaging conventions.
