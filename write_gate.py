"""Opt-in gate for WeChat write actions that Hermes approves one use at a time.

The standalone servers have no approval channel, so favorite / friend-accept /
file sending stay off unless the operator enables them. File sending also needs
an allow-directory, so a prompted client cannot send arbitrary host files.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from phone_control.adb_backend import read_attachment

ENABLE_ENV = "PHONE_MCP_ENABLE_WECHAT_WRITES"
ATTACHMENT_DIR_ENV = "PHONE_MCP_ATTACHMENT_DIR"


def blocked_reason(action: str) -> Optional[str]:
    if os.environ.get(ENABLE_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return None
    return (f"'{action}' is disabled: the standalone server has no approval "
            f"channel. Set {ENABLE_ENV}=1 to allow it.")


def stage_request(path: str) -> tuple[Optional[dict], Optional[str]]:
    """Return (attachment metadata, error) for a file inside the allow-directory."""
    root = os.environ.get(ATTACHMENT_DIR_ENV, "").strip()
    if not root:
        return None, f"file sending needs {ATTACHMENT_DIR_ENV} to name the allowed directory"
    try:
        base = Path(root).expanduser().resolve(strict=True)
        # resolve() follows symlinks, so an escape through one is rejected here.
        target = Path(path).expanduser().resolve(strict=True)
        if not target.is_relative_to(base):
            return None, "file is outside the allowed attachment directory"
        metadata, _ = read_attachment(str(target))
        return metadata, None
    except (OSError, ValueError) as exc:
        return None, str(exc)
