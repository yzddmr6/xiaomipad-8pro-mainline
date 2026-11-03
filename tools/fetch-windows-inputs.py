#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch the locked public firmware sources, host tools and signed driver asset."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    lock = json.loads((repo / "tools/windows.lock.json").read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    for item in [*lock["host_debs"], lock["drivers"]]:
        path = args.output / item["filename"]
        if not path.exists():
            request = urllib.request.Request(
                item.get("asset_url", item["url"]),
                headers={"Accept": "application/octet-stream", "User-Agent": "piano-vm-build"},
            )
            temporary = path.with_suffix(".part")
            try:
                with urllib.request.urlopen(request, timeout=60) as source, temporary.open(
                    "wb"
                ) as out:
                    import shutil

                    shutil.copyfileobj(source, out)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                raise ValueError("Locked input checksum differs: " + path.name)
    for item in lock["firmware_repositories"]:
        root = args.output / "firmware" / item["path"]
        root.mkdir(parents=True, exist_ok=True)
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            ["git", "-C", str(root), "fetch", "--depth=1", item["url"], item["commit"]], check=True
        )
        subprocess.run(["git", "-C", str(root), "checkout", "--detach", item["commit"]], check=True)


if __name__ == "__main__":
    main()
