import json
from pathlib import Path

import pytest

from scripts import sync_from_hermes


def _write_manifest(source: Path, entries: list[dict], helper: dict | None = None) -> None:
    manifest = {
        "schema_version": 1,
        "source_repository": "Ctrl-Creeper/hermes-phone-agent",
        "core_files": entries,
        "helper": helper or {
            "asset": "hermes-phone-agent-v9.9.9.apk",
            "package": "com.hermes.phoneagent",
            "release_tag": "v9.9.9",
            "sha256": "a" * 64,
            "transport_contract": "helper-socket-hmac-v1",
            "version": "9.9.9",
            "version_code": 999,
        },
    }
    (source / "sync").mkdir()
    (source / "sync" / "phone-control-manifest.json").write_text(json.dumps(manifest))


def test_sync_rejects_manifest_mapping_outside_shared_core(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, [{"source": "plugins/phone_use/wechat.py", "target": "mcp_server.py"}])
    (source / "plugins" / "phone_use").mkdir(parents=True)
    (source / "plugins" / "phone_use" / "wechat.py").write_text("unexpected")
    monkeypatch.setattr(sync_from_hermes, "_source_commit", lambda _: "f" * 40)

    with pytest.raises(RuntimeError, match="not approved"):
        sync_from_hermes.sync(source, check=True)


def test_sync_rejects_symlinked_upstream_file(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, [{"source": "plugins/phone_use/wechat.py", "target": "phone_control/wechat.py"}])
    (source / "plugins" / "phone_use").mkdir(parents=True)
    external = tmp_path / "outside.py"
    external.write_text("unexpected")
    (source / "plugins" / "phone_use" / "wechat.py").symlink_to(external)
    monkeypatch.setattr(sync_from_hermes, "_source_commit", lambda _: "f" * 40)

    with pytest.raises(RuntimeError, match="not a regular file"):
        sync_from_hermes.sync(source, check=True)


def test_helper_install_blocks_are_rendered_from_manifest(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, [])

    for name in ("README.md", "README_CN.md"):
        (tmp_path / name).write_text(
            "before\n<!-- helper-apk-install:start -->\nold\n<!-- helper-apk-install:end -->\nafter\n"
        )

    monkeypatch.setattr(sync_from_hermes, "ROOT", tmp_path)
    monkeypatch.setattr(sync_from_hermes, "LOCK_PATH", tmp_path / "upstream.lock.json")
    monkeypatch.setattr(sync_from_hermes, "_source_commit", lambda _: "f" * 40)

    changed = sync_from_hermes.sync(source, check=False)

    assert {"README.md", "README_CN.md"}.issubset(changed)
    for name in ("README.md", "README_CN.md"):
        content = (tmp_path / name).read_text()
        assert "hermes-phone-agent-v9.9.9.apk" in content
        assert "releases/download/v9.9.9/" in content
    assert "可选：安装 Hermes Phone Agent" in (tmp_path / "README_CN.md").read_text()
