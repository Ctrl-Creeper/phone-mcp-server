#!/usr/bin/env python3
"""Synchronize the shared phone-control core from hermes-phone-agent.

The upstream manifest is the allowlist. Hermes-only plugin and gateway code
must never be copied into this standalone MCP server.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "sync/phone-control-manifest.json"
LOCK_PATH = ROOT / "upstream.lock.json"

# The upstream manifest selects a version of this contract. It cannot expand
# the contract to overwrite downstream workflow, server, or packaging files.
ALLOWED_CORE_FILES = frozenset({
    ("plugins/phone_use/backend.py", "phone_control/backend.py"),
    ("plugins/phone_use/adb_backend.py", "phone_control/adb_backend.py"),
    ("plugins/phone_use/appium_backend.py", "phone_control/appium_backend.py"),
    ("plugins/phone_use/appium_manager.py", "phone_control/appium_manager.py"),
    ("plugins/phone_use/host_ocr.py", "phone_control/host_ocr.py"),
    ("plugins/phone_use/native/phone_ocr.swift", "phone_control/phone_ocr.swift"),
    ("plugins/phone_use/policy.py", "phone_control/policy.py"),
    ("plugins/phone_use/sanitize.py", "phone_control/sanitize.py"),
    ("plugins/phone_use/wechat.py", "phone_control/wechat.py"),
    ("plugins/phone_use/wechat_context.py", "phone_control/wechat_context.py"),
    ("phone-policy.yaml", "phone-policy.yaml"),
})
HELPER_BLOCK_START = "<!-- helper-apk-install:start -->"
HELPER_BLOCK_END = "<!-- helper-apk-install:end -->"


def _safe_relative(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe manifest path: {value!r}")
    return Path(*path.parts)


def _load_manifest(source: Path) -> dict[str, Any]:
    manifest_file = source / MANIFEST_PATH
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"upstream manifest not found: {manifest_file}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid upstream manifest: {exc}") from exc
    if data.get("schema_version") != 1:
        raise RuntimeError("unsupported upstream manifest schema")
    if data.get("source_repository") != "Ctrl-Creeper/hermes-phone-agent":
        raise RuntimeError("unexpected upstream repository")
    if not isinstance(data.get("core_files"), list) or not isinstance(data.get("helper"), dict):
        raise RuntimeError("upstream manifest is missing required fields")
    return data


def _source_commit(source: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
    ).strip()


def _lock_data(source: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "upstream_repository": manifest["source_repository"],
        "upstream_commit": _source_commit(source),
        "helper": manifest["helper"],
        "core_files": manifest["core_files"],
    }


def _write_if_changed(path: Path, data: bytes, check: bool) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    if check:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def _helper_install_block(helper: dict[str, Any], chinese: bool) -> str:
    asset = helper.get("asset")
    release_tag = helper.get("release_tag")
    if not isinstance(asset, str) or not isinstance(release_tag, str):
        raise RuntimeError("helper asset and release_tag must be strings")
    url = (
        "https://github.com/Ctrl-Creeper/hermes-phone-agent/releases/download/"
        f"{release_tag}/{asset}"
    )
    description = (
        "# 可选：安装 Hermes Phone Agent，以支持安全的 Unicode/特殊字符输入"
        if chinese
        else "# Optional: install Hermes Phone Agent for secure Unicode/special-character input"
    )
    return (
        f"{HELPER_BLOCK_START}\n"
        f"{description}\n"
        f"curl -fL -o {asset} \\\n"
        f"  {url}\n"
        f"adb install -r {asset}\n"
        f"{HELPER_BLOCK_END}"
    )


def _sync_helper_docs(helper: dict[str, Any], check: bool) -> list[str]:
    changed: list[str] = []
    pattern = re.compile(
        rf"{re.escape(HELPER_BLOCK_START)}.*?{re.escape(HELPER_BLOCK_END)}",
        re.DOTALL,
    )
    for name, chinese in (("README.md", False), ("README_CN.md", True)):
        path = ROOT / name
        content = path.read_text(encoding="utf-8")
        block = _helper_install_block(helper, chinese)
        updated, replacements = pattern.subn(block, content)
        if replacements != 1:
            raise RuntimeError(f"{name} must contain exactly one helper install block")
        if _write_if_changed(path, updated.encode(), check):
            changed.append(name)
    return changed


def sync(source: Path, check: bool) -> list[str]:
    source = source.resolve()
    manifest = _load_manifest(source)
    changed: list[str] = []
    seen_targets: set[Path] = set()

    for entry in manifest["core_files"]:
        if not isinstance(entry, dict):
            raise RuntimeError("core_files entries must be objects")
        source_value = str(entry.get("source", ""))
        target_value = str(entry.get("target", ""))
        if (source_value, target_value) not in ALLOWED_CORE_FILES:
            raise RuntimeError(f"manifest mapping is not approved: {source_value} -> {target_value}")
        source_rel = _safe_relative(source_value)
        target_rel = _safe_relative(target_value)
        if target_rel in seen_targets:
            raise RuntimeError(f"duplicate target in manifest: {target_rel}")
        seen_targets.add(target_rel)
        source_file = source / source_rel
        target_file = ROOT / target_rel
        if not source_file.is_file():
            raise RuntimeError(f"upstream source is missing: {source_rel}")
        if source_file.is_symlink() or not source_file.resolve().is_relative_to(source):
            raise RuntimeError(f"upstream source is not a regular file within its checkout: {source_rel}")
        if _write_if_changed(target_file, source_file.read_bytes(), check):
            changed.append(str(target_rel))

    changed.extend(_sync_helper_docs(manifest["helper"], check))
    lock_bytes = (json.dumps(_lock_data(source, manifest), indent=2, sort_keys=True) + "\n").encode()
    if _write_if_changed(LOCK_PATH, lock_bytes, check):
        changed.append(LOCK_PATH.name)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="hermes-phone-agent checkout")
    parser.add_argument("--check", action="store_true", help="fail instead of writing drift")
    args = parser.parse_args()
    try:
        changed = sync(args.source, args.check)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 2
    if args.check and changed:
        print("out of sync: " + ", ".join(changed), file=sys.stderr)
        return 1
    print("already synchronized" if not changed else "synchronized: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
