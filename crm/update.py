"""`crm update` — self-update from GitHub Releases.

Hits api.github.com/.../releases/latest, picks the right asset for the
platform, downloads it, and atomically replaces the running binary
(os.replace works under a live process on POSIX — the running image
keeps the old inode open until it exits).

Refuses to operate in two cases:
- Running from a Python interpreter (source/dev mode) — there's no
  binary to swap; user should `git pull` or `pip install --upgrade`.
- No write permission on the binary's directory — typically
  /usr/local/bin requires sudo.

Version comparison is a simple tuple-of-ints; pre-release suffixes
(rc1, dev0, …) are ignored. Pre-release users can re-curl manually.
"""

import json
import os
import platform
import shutil
import stat
import sys
import tempfile
import urllib.request

from . import __version__

GITHUB_API = "https://api.github.com/repos/Kitron-Consulting/crm/releases/latest"


def _asset_name():
    system = platform.system()
    machine = platform.machine()
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "crm-linux-x86_64"
    if system == "Darwin" and machine == "arm64":
        return "crm-macos-arm64"
    return None


def _fetch_latest():
    req = urllib.request.Request(
        GITHUB_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"crm-updater/{__version__}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    tag = data["tag_name"]
    version = tag.lstrip("v")
    asset_name = _asset_name()
    if asset_name is None:
        raise RuntimeError(
            f"No prebuilt asset for {platform.system()}/{platform.machine()}. "
            "Build from source: pip install --upgrade git+https://github.com/Kitron-Consulting/crm"
        )
    for asset in data["assets"]:
        if asset["name"] == asset_name:
            return version, asset["browser_download_url"], asset["size"]
    raise RuntimeError(f"Asset '{asset_name}' missing from release {tag}.")


def _numeric_parts(v):
    """Tuple of leading numeric components: '1.5.0rc1' -> (1, 5)."""
    nums = []
    for chunk in v.split("+")[0].replace("-", ".").split("."):
        try:
            nums.append(int(chunk))
        except ValueError:
            break
    return tuple(nums)


def _is_newer(latest, current):
    return _numeric_parts(latest) > _numeric_parts(current)


def _binary_path():
    """Path of the currently running binary, with symlinks resolved.
    Returns None if we're running from source (python -m crm)."""
    argv0 = sys.argv[0]
    if not argv0:
        return None
    resolved = os.path.realpath(argv0)
    name = os.path.basename(resolved)
    # If invoked as `python -m crm`, argv[0] is the python interpreter path.
    if name.startswith("python") or name.endswith(".py"):
        return None
    return resolved


def run(args):
    check_only = "--check" in args

    print(f"Current version: {__version__}")
    print("Checking GitHub…")
    try:
        latest, url, size = _fetch_latest()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"Latest version:  {latest}")

    if not _is_newer(latest, __version__):
        print("Already up to date.")
        return

    print(f"Update available: {__version__} → {latest}  ({size // 1024 // 1024} MB)")

    binary_path = _binary_path()
    if binary_path is None:
        print(
            "Running from source — `crm update` can only swap a binary. "
            "Upgrade with `git pull && pip install --upgrade .` instead."
        )
        # Exit code 0 if --check (informational), nonzero if user expected an install.
        sys.exit(0 if check_only else 1)

    if check_only:
        print("Run `crm update` to install.")
        return

    bin_dir = os.path.dirname(binary_path)
    if not os.access(bin_dir, os.W_OK):
        print(f"Error: no write permission for {binary_path}.")
        print(f"Try: sudo crm update")
        sys.exit(1)

    print(f"Downloading {url}…")
    try:
        with urllib.request.urlopen(url, timeout=300) as r:
            with tempfile.NamedTemporaryFile(
                dir=bin_dir, prefix=".crm.update-", delete=False
            ) as tmp:
                shutil.copyfileobj(r, tmp)
                tmp_path = tmp.name
    except Exception as e:
        print(f"Error: download failed: {e}")
        sys.exit(1)

    os.chmod(
        tmp_path,
        os.stat(tmp_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )
    os.replace(tmp_path, binary_path)
    print(f"Updated to v{latest}. The next `crm` invocation will use the new binary.")
