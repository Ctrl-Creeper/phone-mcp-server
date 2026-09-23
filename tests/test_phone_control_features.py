import json
from types import SimpleNamespace

from phone_control.backend import ActionResult, CaptureResult, UIElement
from phone_control.wechat import _find_text, _normalize_chat_title
from phone_control.wechat_context import parse_collection_scope, merge_older_lines
import http_server
import mcp_server


def test_wechat_title_normalization_removes_member_count_and_ocr_noise():
    assert _normalize_chat_title("• Example Group（24 members）杂") == "•examplegroup"
    assert _normalize_chat_title("◆") == "◆"
    assert _find_text(
        [UIElement(index=1, class_name="host.ocr.Text", text="Examp1e Group（24）", bounds=(0, 300, 500, 380))],
        "Example Group",
    ).index == 1


def test_collection_scope_supports_recent_history_phrases_and_explicit_limits():
    default = parse_collection_scope("刚刚发生了啥")
    assert (default.max_messages, default.max_minutes, default.max_pages) == (50, 10, 8)
    explicit = parse_collection_scope("总结最近20条")
    assert explicit.max_messages == 20 and explicit.explicit


def test_context_pages_merge_only_their_overlap():
    assert merge_older_lines(["c", "d"], ["a", "b", "c"]) == ["a", "b", "c", "d"]


def test_capture_result_is_json_serializable():
    result = ActionResult(
        ok=True,
        action="wechat_collect_context",
        meta={"coverage": "complete", "lines": ["hello"]},
        capture=CaptureResult(
            mode="hierarchy", width=1080, height=2400,
        elements=[UIElement(index=1, class_name="host.ocr.Text", text="hello", bounds=(1, 2, 3, 4))],
        ),
    )
    assert json.loads(json.dumps(result.meta))["coverage"] == "complete"


def test_standalone_servers_block_actions_that_require_hermes_approval(monkeypatch):
    policy = SimpleNamespace(
        requires_approval=lambda action: action == "wechat_reply",
        check_action=lambda action, package: SimpleNamespace(action_allowed=lambda value: True),
    )
    monkeypatch.setattr(http_server, "get_policy", lambda: policy)
    monkeypatch.setattr(mcp_server, "get_policy", lambda: policy)
    assert http_server._policy_check("wechat_reply", "com.tencent.mm")["error"]
    assert json.loads(mcp_server._policy_check("wechat_reply", "com.tencent.mm"))["error"]


def test_context_entrypoints_default_to_one_text_page_and_keep_explicit_history(monkeypatch):
    observed = []

    def collect(_backend, _chat, **kwargs):
        observed.append(kwargs)
        return ActionResult(ok=True, action="wechat_collect_context", meta={"screenshots": []})

    monkeypatch.setattr(mcp_server, "_get_backend", lambda: object())
    monkeypatch.setattr(mcp_server, "_policy_check", lambda *_: None)
    monkeypatch.setattr(mcp_server, "collect_wechat_context", collect)
    monkeypatch.setattr(http_server, "_policy_check", lambda *_: None)
    monkeypatch.setattr(http_server, "collect_wechat_context", collect)

    mcp_server.phone_wechat_collect_context("Example")
    http_server._dispatch(object(), "wechat_collect_context", {"chat": "Example"})
    assert all(call["max_pages"] == 1 and not call["include_images"]
               and not call["open_images"] for call in observed)

    mcp_server.phone_wechat_collect_context("Example", scope="最近20条")
    http_server._dispatch(object(), "wechat_collect_context", {
        "chat": "Example", "include_images": True, "open_images": True,
    })
    assert observed[-2]["max_pages"] == 8 and not observed[-2]["open_images"]
    assert observed[-1]["max_pages"] == 8 and observed[-1]["include_images"]
    assert observed[-1]["open_images"]
