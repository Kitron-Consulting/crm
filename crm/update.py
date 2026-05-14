"""`crm update` — self-update from GitHub Releases.

Hits api.github.com/.../releases/latest, picks the right asset for the
platform, downloads it, and atomically replaces the running binary with
os.replace (POSIX allows this under a live process — the running image
keeps the old inode open).

Every failure path prints a manual-install fallback so the user has a
clean recovery: either the curl command for the resolved asset URL, or
the releases page if we couldn't even reach the API.
"""

import json
import os
import platform
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.request

from . import __version__

GITHUB_API = "https://api.github.com/repos/Kitron-Consulting/crm/releases/latest"
RELEASES_URL = "https://github.com/Kitron-Consulting/crm/releases/latest"


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
        raw = r.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GitHub returned non-JSON: {e}") from e
    if not isinstance(data, dict) or "tag_name" not in data or "assets" not in data:
        raise RuntimeError(
            "GitHub response missing expected fields (tag_name / assets); "
            "the API format may have changed."
        )
    tag = data["tag_name"]
    version = tag.lstrip("v")
    asset_name = _asset_name()
    if asset_name is None:
        raise RuntimeError(
            f"no prebuilt asset for {platform.system()}/{platform.machine()}"
        )
    for asset in data["assets"]:
        if asset["name"] == asset_name:
            return version, asset["browser_download_url"], asset["size"]
    raise RuntimeError(f"asset '{asset_name}' missing from release {tag}")


def _manual_install_hint(binary_path=None, asset_url=None):
    """Recovery hint: exact curl command if we have the URL, else the releases page."""
    if asset_url and binary_path:
        return (
            "  Install manually:\n"
            f"    curl -L -o {binary_path} \\\n"
            f"      {asset_url}\n"
            f"    chmod +x {binary_path}"
        )
    return f"  See latest assets at {RELEASES_URL}"


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
    """Path of the currently running binary, or None if running from source."""
    argv0 = sys.argv[0]
    if not argv0:
        return None
    resolved = os.path.realpath(argv0)
    name = os.path.basename(resolved)
    if name.startswith("python") or name.endswith(".py"):
        return None
    return resolved


def run(args):
    check_only = "--check" in args

    print(f"Current version: {__version__}")
    print(f"Checking {GITHUB_API}…")

    try:
        latest, asset_url, size = _fetch_latest()
    except urllib.error.HTTPError as e:
        msg = f"GitHub returned HTTP {e.code} {e.reason}"
        if e.code == 404:
            msg += " — the repository or release may have moved."
        elif e.code == 403:
            msg += " — likely rate limited; try again in ~1 hour."
        print(f"Error: {msg}")
        print(_manual_install_hint())
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: network failure reaching {GITHUB_API}: {e.reason}")
        print("Check your connection and retry.")
        print(_manual_install_hint())
        sys.exit(1)
    except (RuntimeError, TypeError, KeyError, ValueError) as e:
        print(f"Error: {e}")
        print(_manual_install_hint())
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
        sys.exit(0 if check_only else 1)

    if check_only:
        print("Run `crm update` to install.")
        return

    bin_dir = os.path.dirname(binary_path)
    if not os.access(bin_dir, os.W_OK):
        print(f"Error: no write permission for {binary_path}")
        print("  Try: sudo crm update")
        print(_manual_install_hint(binary_path, asset_url))
        sys.exit(1)

    print(f"Downloading {asset_url}…")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=bin_dir, prefix=".crm.update-", delete=False
        ) as tmp:
            tmp_path = tmp.name
            with urllib.request.urlopen(asset_url, timeout=300) as r:
                shutil.copyfileobj(r, tmp)
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        print(f"Error: download failed: {e}")
        print(_manual_install_hint(binary_path, asset_url))
        sys.exit(1)

    os.chmod(
        tmp_path,
        os.stat(tmp_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )
    os.replace(tmp_path, binary_path)
    print(f"Updated to v{latest}. The next `crm` invocation will use the new binary.")
