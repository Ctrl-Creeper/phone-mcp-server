#!/usr/bin/env python3
"""Synchronize the shared phone-control core from hermes-phone-agent.

The upstream manifest is the allowlist. Hermes-only plugin and gateway code
must never be copied into this standalone MCP server.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "sync/phone-control-manifest.json"
LOCK_PATH = ROOT / "upstream.lock.json"


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


def sync(source: Path, check: bool) -> list[str]:
    source = source.resolve()
    manifest = _load_manifest(source)
    changed: list[str] = []
    seen_targets: set[Path] = set()

    for entry in manifest["core_files"]:
        if not isinstance(entry, dict):
            raise RuntimeError("core_files entries must be objects")
        source_rel = _safe_relative(str(entry.get("source", "")))
        target_rel = _safe_relative(str(entry.get("target", "")))
        if target_rel in seen_targets:
            raise RuntimeError(f"duplicate target in manifest: {target_rel}")
        seen_targets.add(target_rel)
        source_file = source / source_rel
        target_file = ROOT / target_rel
        if not source_file.is_file():
            raise RuntimeError(f"upstream source is missing: {source_rel}")
        if _write_if_changed(target_file, source_file.read_bytes(), check):
            changed.append(str(target_rel))

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
