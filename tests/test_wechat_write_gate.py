import json
import os

import pytest

import http_server
import mcp_server
import write_gate
from phone_control.backend import ActionResult

WRITES = {
    "wechat_favorite": {"chat": "C", "message_text": "hi"},
    "wechat_send_attachment": {"chat": "C", "file_path": "x"},
    "wechat_accept_friend": {"requester": "Bob"},
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for key in (write_gate.ENABLE_ENV, write_gate.ATTACHMENT_DIR_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(http_server, "_policy_check", lambda *_: None)
    monkeypatch.setattr(mcp_server, "_policy_check", lambda *_: None)
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: object())


def _boom(*_a, **_k):
    raise AssertionError("write action ran while disabled")


@pytest.mark.parametrize("action,body", WRITES.items())
def test_write_actions_are_off_by_default(monkeypatch, action, body):
    for name in ("favorite_wechat_message", "send_wechat_attachment", "accept_wechat_friend"):
        monkeypatch.setattr(http_server, name, _boom)
        monkeypatch.setattr(mcp_server, name, _boom)
    assert "disabled" in http_server._dispatch(object(), action, body)["reason"]
    calls = {
        "wechat_favorite": lambda: mcp_server.phone_wechat_favorite("C", "hi"),
        "wechat_send_attachment": lambda: mcp_server.phone_wechat_send_attachment("C", "x"),
        "wechat_accept_friend": lambda: mcp_server.phone_wechat_accept_friend("Bob"),
    }
    assert "disabled" in json.loads(calls[action]())["reason"]


def test_attachment_must_live_inside_allowed_dir(monkeypatch, tmp_path):
    allowed, outside = tmp_path / "ok", tmp_path / "no"
    allowed.mkdir(), outside.mkdir()
    (allowed / "a.txt").write_text("data")
    (outside / "b.txt").write_text("secret")
    (allowed / "link.txt").symlink_to(outside / "b.txt")
    monkeypatch.setenv(write_gate.ATTACHMENT_DIR_ENV, str(allowed))

    meta, error = write_gate.stage_request(str(allowed / "a.txt"))
    assert error is None and meta["size"] == 4
    assert write_gate.stage_request(str(outside / "b.txt"))[1]
    assert write_gate.stage_request(str(allowed / "link.txt"))[1]  # symlink escape
    # Missing and outside-but-existing paths must be indistinguishable.
    assert (write_gate.stage_request(str(outside / "b.txt"))[1]
            == write_gate.stage_request(str(outside / "missing.txt"))[1]
            == write_gate.stage_request(str(allowed / "link.txt"))[1])
    monkeypatch.delenv(write_gate.ATTACHMENT_DIR_ENV)
    assert write_gate.stage_request(str(allowed / "a.txt"))[1]  # no allow-dir = disabled


def test_enabled_send_attachment_passes_verified_hash(monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("data")
    monkeypatch.setenv(write_gate.ENABLE_ENV, "1")
    monkeypatch.setenv(write_gate.ATTACHMENT_DIR_ENV, str(tmp_path))
    seen = []
    monkeypatch.setattr(http_server, "send_wechat_attachment",
                        lambda b, chat, path, sha: seen.append((chat, path, sha)) or
                        ActionResult(ok=True, action="wechat_send_attachment"))
    out = http_server._dispatch(object(), "wechat_send_attachment",
                                {"chat": "C", "file_path": str(tmp_path / "a.txt")})
    assert out["ok"] and len(seen[0][2]) == 64


def test_quote_is_forwarded_only_when_present(monkeypatch):
    seen = []
    fake = lambda b, chat, text, **kw: seen.append(kw) or ActionResult(ok=True, action="r")
    monkeypatch.setattr(http_server, "reply_to_wechat", fake)
    monkeypatch.setattr(mcp_server, "reply_to_wechat", fake)
    http_server._dispatch(object(), "wechat_reply", {"chat": "C", "text": "t"})
    mcp_server.phone_wechat_reply("C", "t")
    http_server._dispatch(object(), "wechat_reply", {"chat": "C", "text": "t", "quote_text": "q"})
    mcp_server.phone_wechat_reply("C", "t", quote_text="q", quote_max_pages=5)
    assert seen[0] == seen[1] == {}
    assert seen[2]["quote_text"] == "q" and seen[3]["quote_max_pages"] == 5


def test_search_history_is_ungated_and_openai_names_map(monkeypatch):
    monkeypatch.setattr(http_server, "search_wechat_history",
                        lambda b, chat, q, max_pages: ActionResult(ok=True, action="s", meta={"q": q}))
    assert http_server._dispatch(object(), "wechat_search_history",
                                 {"chat": "C", "query": "x"})["ok"]
    for action in WRITES | {"wechat_search_history": 0}:
        assert action in http_server._OPENAI_NAME_TO_ACTION.values()
