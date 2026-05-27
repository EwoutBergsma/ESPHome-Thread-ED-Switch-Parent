#!/usr/bin/env python3
"""Patch ESP-IDF's vendored OpenThread ping sender to expose RSS.

This script is registered by the ESPHome ``thread_ping`` external component as a
PlatformIO pre-build script. It patches the OpenThread copy inside the active
ESP-IDF framework package so:

* ``otPingSenderReply`` contains ``mRss``;
* ``PingSender::HandleIcmpReceive()`` copies RSS from the received message;
* the built-in OpenThread CLI ping line prints RSS when available.

The patch is intentionally idempotent. It creates ``.thread-ping-rss.bak``
backups next to files before changing them.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

BACKUP_SUFFIX = ".thread-ping-rss.bak"


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def read_text(path: Path) -> str:
    return normalize(path.read_text(encoding="utf-8"))


def backup_once(path: Path) -> None:
    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)


def write_if_changed(path: Path, old: str, new: str) -> str:
    if old == new:
        return "already"
    backup_once(path)
    path.write_text(new, encoding="utf-8", newline="\n")
    return "patched"


def get_platformio_env():
    try:  # PlatformIO/SCons injects Import into the script namespace.
        # noqa: F821 - Import is provided by SCons at runtime.
        Import("env")  # type: ignore[name-defined]
        return env  # type: ignore[name-defined]
    except Exception:
        return None


def framework_espidf_roots_from_env() -> list[Path]:
    roots: list[Path] = []
    env = get_platformio_env()
    if env is None:
        return roots

    try:
        package_dir = env.PioPlatform().get_package_dir("framework-espidf")
        if package_dir:
            roots.append(Path(package_dir))
    except Exception:
        pass

    for key in ("PROJECT_PACKAGES_DIR", "PIOPACKAGES_DIR"):
        try:
            value = env.get(key)
        except Exception:
            value = None
        if value:
            roots.append(Path(str(value)) / "framework-espidf")

    try:
        packages_dir = env.subst("$PROJECT_PACKAGES_DIR")
    except Exception:
        packages_dir = ""
    if packages_dir and "$" not in packages_dir:
        roots.append(Path(packages_dir) / "framework-espidf")

    return roots


def candidate_framework_roots() -> Iterable[Path]:
    seen: set[Path] = set()

    candidates: list[Path] = []
    candidates.extend(framework_espidf_roots_from_env())

    env_packages = os.environ.get("PLATFORMIO_PACKAGES_DIR")
    if env_packages:
        candidates.append(Path(env_packages) / "framework-espidf")

    home = Path.home()
    candidates.append(home / ".platformio" / "packages" / "framework-espidf")

    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidates.append(parent / ".platformio" / "packages" / "framework-espidf")
        candidates.append(parent / "packages" / "framework-espidf")

    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            yield candidate


def find_openthread_root(explicit: Optional[Path] = None) -> Optional[Path]:
    if explicit is not None:
        explicit = explicit.expanduser().resolve()
        if (explicit / "include" / "openthread" / "ping_sender.h").exists():
            return explicit
        if (explicit / "components" / "openthread" / "openthread" / "include" / "openthread" / "ping_sender.h").exists():
            return explicit / "components" / "openthread" / "openthread"

    for framework_root in candidate_framework_roots():
        direct = framework_root / "components" / "openthread" / "openthread"
        if (direct / "include" / "openthread" / "ping_sender.h").exists():
            return direct

        # Fallback for layout changes: search the framework package for the public header.
        for header in framework_root.rglob("include/openthread/ping_sender.h"):
            return header.parents[2]

    return None


def patch_ping_sender_header(root: Path) -> str:
    path = root / "include" / "openthread" / "ping_sender.h"
    text = read_text(path)

    if re.search(r"typedef\s+struct\s+otPingSenderReply\s*\{.*?\bmRss\b.*?\}\s*otPingSenderReply\s*;", text, re.S):
        return "already"

    pattern = re.compile(
        r"(typedef\s+struct\s+otPingSenderReply\s*\{.*?\n)"
        r"(\s*uint8_t\s+mHopLimit\s*;[^\n]*\n)"
        r"(\s*\}\s*otPingSenderReply\s*;)",
        re.S,
    )

    replacement = (
        r"\1"
        r"\2"
        "    int8_t       mRss;           ///< Average RSS of the received Echo Reply in dBm, or OT_RADIO_RSSI_INVALID if unavailable.\n"
        r"\3"
    )
    new_text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        return "missing"
    return write_if_changed(path, text, new_text)


def patch_ping_sender_cpp(root: Path) -> str:
    path = root / "src" / "core" / "utils" / "ping_sender.cpp"
    text = read_text(path)

    if "reply.mRss" in text:
        return "already"

    pattern = re.compile(r"(\n\s*reply\.mHopLimit\s*=\s*aMessageInfo\.GetHopLimit\(\)\s*;)")
    replacement = r"\1\n    reply.mRss            = aMessage.GetAverageRss();"
    new_text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        return "missing"
    return write_if_changed(path, text, new_text)


def ensure_radio_include(text: str) -> str:
    if "#include <openthread/platform/radio.h>" in text:
        return text
    if "#include <openthread/ping_sender.h>" in text:
        return text.replace(
            "#include <openthread/ping_sender.h>\n",
            "#include <openthread/ping_sender.h>\n#include <openthread/platform/radio.h>\n",
            1,
        )
    return text


def patch_cli_ping_file(path: Path) -> str:
    if not path.exists():
        return "not-found"

    text = read_text(path)
    if "rss=unavailable" in text or ("mRss" in text and "time=%ums rss=%d" in text):
        return "already"

    text_with_include = ensure_radio_include(text)

    replacement = """if (aReply->mRss == OT_RADIO_RSSI_INVALID)
    {
        OutputLine(": icmp_seq=%u hlim=%u time=%ums rss=unavailable", aReply->mSequenceNumber, aReply->mHopLimit,
                   aReply->mRoundTripTime);
    }
    else
    {
        OutputLine(": icmp_seq=%u hlim=%u time=%ums rss=%d", aReply->mSequenceNumber, aReply->mHopLimit,
                   aReply->mRoundTripTime, aReply->mRss);
    }"""

    # Current OpenThread src/cli/cli_ping.cpp shape.
    pattern_current = re.compile(
        r"OutputLine\(\s*\": icmp_seq=%u hlim=%u time=%ums\"\s*,\s*"
        r"aReply->mSequenceNumber\s*,\s*aReply->mHopLimit\s*,\s*aReply->mRoundTripTime\s*\)\s*;"
    )
    new_text, count = pattern_current.subn(replacement, text_with_include, count=1)
    if count == 1:
        return write_if_changed(path, text, new_text)

    # Older CLI implementation may use aReply as a value/reference instead of a pointer.
    replacement_value = """if (aReply.mRss == OT_RADIO_RSSI_INVALID)
    {
        OutputLine(": icmp_seq=%u hlim=%u time=%ums rss=unavailable", aReply.mSequenceNumber, aReply.mHopLimit,
                   aReply.mRoundTripTime);
    }
    else
    {
        OutputLine(": icmp_seq=%u hlim=%u time=%ums rss=%d", aReply.mSequenceNumber, aReply.mHopLimit,
                   aReply.mRoundTripTime, aReply.mRss);
    }"""
    pattern_value = re.compile(
        r"OutputLine\(\s*\": icmp_seq=%u hlim=%u time=%ums\"\s*,\s*"
        r"aReply\.mSequenceNumber\s*,\s*aReply\.mHopLimit\s*,\s*aReply\.mRoundTripTime\s*\)\s*;"
    )
    new_text, count = pattern_value.subn(replacement_value, text_with_include, count=1)
    if count == 1:
        return write_if_changed(path, text, new_text)

    return "missing"


def patch_cli(root: Path) -> str:
    candidates = [
        root / "src" / "cli" / "cli_ping.cpp",
        root / "src" / "cli" / "cli.cpp",
    ]
    results = []
    for candidate in candidates:
        result = patch_cli_ping_file(candidate)
        if result != "not-found":
            results.append(f"{candidate.name}:{result}")
    return ", ".join(results) if results else "not-found"


def apply(root: Path) -> int:
    print(f"[thread_ping] OpenThread root: {root}")
    results = {
        "ping_sender.h": patch_ping_sender_header(root),
        "ping_sender.cpp": patch_ping_sender_cpp(root),
        "cli ping": patch_cli(root),
    }
    for name, result in results.items():
        print(f"[thread_ping] {name}: {result}")

    required_missing = [name for name in ("ping_sender.h", "ping_sender.cpp") if results[name] == "missing"]
    if required_missing:
        print(f"[thread_ping] ERROR: required OpenThread patch point(s) missing: {', '.join(required_missing)}")
        return 2
    return 0


def main(argv: list[str]) -> int:
    explicit = Path(argv[1]) if len(argv) > 1 else None
    root = find_openthread_root(explicit)
    if root is None:
        print("[thread_ping] ERROR: could not find ESP-IDF vendored OpenThread tree")
        return 2
    return apply(root)


_exit_code = main(sys.argv)
if _exit_code != 0:
    raise SystemExit(_exit_code)
