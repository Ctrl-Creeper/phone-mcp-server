#!/usr/bin/env python3
"""HTTP REST API server for phone control.

Universal adapter — any agent framework that can make HTTP calls can
control the phone. Also serves an OpenAI function-calling schema at
GET /openai/tools for GPT-based agents.

Usage:
    python http_server.py                    # localhost:8080
    python http_server.py --port 9000        # custom port
    python http_server.py --host 0.0.0.0     # listen on all interfaces

Endpoints:
    POST /phone/{action}    Execute a phone action (JSON body)
    GET  /phone/status      Connection status
    GET  /openai/tools      OpenAI function-calling tool schema
    POST /openai/call       Execute an OpenAI function call
    GET  /health            Health check
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
from typing import Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from phone_control.backend import (
    ActionResult,
    CaptureResult,
    PhoneBackend,
    UIElement,
)
from phone_control.policy import get_policy

logger = logging.getLogger("phone-http")

# ── Auth ───────────────────────────────────────────────────────────

# API token required on all endpoints except /health.
# Set via PHONE_API_TOKEN env var, or auto-generated at startup.
_API_TOKEN: Optional[str] = None

# Endpoints that don't require auth (liveness check only).
_PUBLIC_PATHS = frozenset({"/health"})


def _get_api_token() -> str:
    global _API_TOKEN
    if _API_TOKEN is None:
        _API_TOKEN = os.environ.get("PHONE_API_TOKEN") or secrets.token_urlsafe(32)
    return _API_TOKEN


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require Authorization: Bearer <token> on all endpoints except /health."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse(
                {"error": "missing Authorization: Bearer <token> header"},
                status_code=401,
            )
        provided = header[len("Bearer "):].strip()
        expected = _get_api_token()
        # Constant-time comparison to avoid timing attacks.
        if not secrets.compare_digest(provided, expected):
            return JSONResponse({"error": "invalid token"}, status_code=403)

        return await call_next(request)


# ── Backend lifecycle ──────────────────────────────────────────────

_backend: Optional[PhoneBackend] = None
_backend_lock = threading.Lock()


def _get_backend() -> PhoneBackend:
    global _backend
    with _backend_lock:
        if _backend is not None:
            return _backend

        backend_name = os.environ.get("HERMES_PHONE_BACKEND", "adb").lower()

        if backend_name == "adb":
            from phone_control.adb_backend import AdbBackend
            backend: PhoneBackend = AdbBackend(serial=os.environ.get("ANDROID_SERIAL"))
        elif backend_name in ("hybrid", "appium"):
            from phone_control.appium_backend import HybridBackend
            backend = HybridBackend(serial=os.environ.get("ANDROID_SERIAL"))
        elif backend_name == "noop":
            backend = _NoopBackend()
        else:
            raise RuntimeError(f"Unknown HERMES_PHONE_BACKEND={backend_name!r}")

        backend.start()
        _backend = backend
        return _backend


class _NoopBackend(PhoneBackend):
    def start(self): pass
    def stop(self): pass
    def is_available(self): return True
    def device_info(self):
        from phone_control.backend import DeviceInfo
        return DeviceInfo(serial="noop", model="Noop", screen_width=1080,
                          screen_height=2400, is_emulator=True)
    def capture(self, mode="som"):
        return CaptureResult(mode=mode, width=1080, height=2400)
    def tap(self, **kw): return ActionResult(ok=True, action="tap")
    def double_tap(self, **kw): return ActionResult(ok=True, action="double_tap")
    def long_press(self, **kw): return ActionResult(ok=True, action="long_press")
    def swipe(self, **kw): return ActionResult(ok=True, action="swipe")
    def type_text(self, text, element=None): return ActionResult(ok=True, action="type")
    def clear_text(self, element=None): return ActionResult(ok=True, action="clear_text")
    def set_text(self, text, element=None): return ActionResult(ok=True, action="set_text")
    def keyevent(self, keycode): return ActionResult(ok=True, action="keyevent")
    def launch_app(self, package, activity=None): return ActionResult(ok=True, action="launch_app")
    def stop_app(self, package): return ActionResult(ok=True, action="stop_app")
    def list_apps(self, installed_only=True): return []
    def current_app(self): return {"package": "com.noop", "activity": ".Main"}
    def install_apk(self, apk_path): return ActionResult(ok=True, action="install_apk")
    def shell(self, command): return ActionResult(ok=True, action="shell")


# ── Helpers ────────────────────────────────────────────────────────

def _element_to_dict(e: UIElement) -> Dict[str, Any]:
    return {
        "index": e.index, "class": e.class_name,
        "resource_id": e.resource_id, "text": e.text,
        "content_desc": e.content_desc, "bounds": list(e.bounds),
        "clickable": e.clickable, "scrollable": e.scrollable, "enabled": e.enabled,
    }


def _action_dict(res: ActionResult) -> Dict[str, Any]:
    d: Dict[str, Any] = {"ok": res.ok, "action": res.action}
    if res.message: d["message"] = res.message
    return d


def _policy_check(action: str, package: str) -> Optional[Dict[str, Any]]:
    if not package: return None
    policy = get_policy()
    decision = policy.check_action(action, package)
    if not decision.action_allowed(action):
        return {
            "error": "blocked by phone policy", "action": action,
            "package": package,
            "reason": decision.notes or f"policy restricts '{action}' on '{package}'",
        }
    return None


def _resolve_target_package(
    backend: PhoneBackend, action: str, body: Dict[str, Any],
) -> str:
    """Resolve the package to check against the policy.

    For launch_app/stop_app, the explicit 'package' arg is the target.
    For all other non-safe actions, use the foreground app — agent-supplied
    'package' is IGNORED to prevent policy bypass.
    """
    if action in _PACKAGE_AWARE_ACTIONS:
        return body.get("package", "")
    try:
        return backend.current_app().get("package", "")
    except Exception:
        return ""


_SAFE_ACTIONS = frozenset({"capture", "wait", "list_apps", "current_app", "device_info"})
_DANGEROUS_ACTIONS = frozenset({"install_apk", "shell"})
_PACKAGE_AWARE_ACTIONS = frozenset({"launch_app", "stop_app"})


# ── Dispatch ───────────────────────────────────────────────────────

def _dispatch(backend: PhoneBackend, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if action in _DANGEROUS_ACTIONS:
        return {"error": f"'{action}' is not allowed via HTTP API for security reasons"}

    if action not in _SAFE_ACTIONS:
        pkg = _resolve_target_package(backend, action, body)
        blocked = _policy_check(action, pkg)
        if blocked: return blocked

    if action == "capture":
        mode = body.get("mode", "hierarchy")
        if mode not in ("hierarchy", "screenshot", "som"):
            return {"error": f"invalid mode: {mode!r}"}
        cap = backend.capture(mode=mode)
        result: Dict[str, Any] = {
            "mode": cap.mode, "width": cap.width, "height": cap.height,
            "foreground": f"{cap.current_package}/{cap.current_activity}",
            "elements": [_element_to_dict(e) for e in cap.elements[:100]],
            "total_elements": len(cap.elements),
        }
        if cap.png_b64 and mode != "hierarchy":
            result["image_base64"] = cap.png_b64
        return result

    if action == "device_info":
        info = backend.device_info()
        return {
            "serial": info.serial, "model": info.model,
            "android_version": info.android_version, "sdk_version": info.sdk_version,
            "screen": f"{info.screen_width}x{info.screen_height}",
            "density": info.density, "is_emulator": info.is_emulator,
        }

    if action == "list_apps":
        apps = backend.list_apps()
        return {"apps": apps, "count": len(apps)}

    if action == "current_app":
        return backend.current_app()

    if action == "wait":
        return _action_dict(backend.wait(float(body.get("seconds", 1.0))))

    element = body.get("element")
    coord = body.get("coordinate")
    x = coord[0] if coord else body.get("x")
    y = coord[1] if coord else body.get("y")

    if action == "tap":
        return _action_dict(backend.tap(element=element, x=x, y=y))
    if action == "double_tap":
        return _action_dict(backend.double_tap(element=element, x=x, y=y))
    if action == "long_press":
        return _action_dict(backend.long_press(element=element, x=x, y=y, duration_ms=int(body.get("duration_ms", 1000))))
    if action == "swipe":
        from_xy = tuple(body["from_coordinate"]) if body.get("from_coordinate") else None
        to_xy = tuple(body["to_coordinate"]) if body.get("to_coordinate") else None
        return _action_dict(backend.swipe(
            direction=body.get("direction"), from_xy=from_xy, to_xy=to_xy,
            duration_ms=int(body.get("duration_ms", 300)), element=element,
        ))
    if action == "type":
        return _action_dict(backend.type_text(body.get("text", ""), element=element))
    if action == "clear_text":
        return _action_dict(backend.clear_text(element=element))
    if action == "set_text":
        return _action_dict(backend.set_text(body.get("text", ""), element=element))
    if action == "keyevent":
        keycode = body.get("keycode")
        if not keycode: return {"error": "keyevent requires 'keycode'"}
        return _action_dict(backend.keyevent(keycode))
    if action == "launch_app":
        package = body.get("package")
        if not package: return {"error": "launch_app requires 'package'"}
        return _action_dict(backend.launch_app(package, activity=body.get("activity")))
    if action == "stop_app":
        package = body.get("package")
        if not package: return {"error": "stop_app requires 'package'"}
        return _action_dict(backend.stop_app(package))

    return {"error": f"unknown action: {action!r}"}


# ── OpenAI function-calling schema ─────────────────────────────────

OPENAI_TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "phone_capture",
        "description": "Capture the phone screen. mode='hierarchy' (recommended) for UI tree, 'screenshot' for image, 'som' for both.",
        "parameters": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["hierarchy", "screenshot", "som"]},
        }},
    }},
    {"type": "function", "function": {
        "name": "phone_tap",
        "description": "Tap on the phone screen. Prefer element index over coordinates.",
        "parameters": {"type": "object", "properties": {
            "element": {"type": "integer", "description": "Element index from last capture"},
            "x": {"type": "integer", "description": "Pixel X"}, "y": {"type": "integer", "description": "Pixel Y"},
        }},
    }},
    {"type": "function", "function": {
        "name": "phone_double_tap",
        "description": "Double-tap on the phone screen.",
        "parameters": {"type": "object", "properties": {
            "element": {"type": "integer"}, "x": {"type": "integer"}, "y": {"type": "integer"},
        }},
    }},
    {"type": "function", "function": {
        "name": "phone_long_press",
        "description": "Long-press on the phone screen.",
        "parameters": {"type": "object", "properties": {
            "element": {"type": "integer"}, "x": {"type": "integer"}, "y": {"type": "integer"},
            "duration_ms": {"type": "integer", "description": "Duration in ms (100-5000, default 1000)"},
        }},
    }},
    {"type": "function", "function": {
        "name": "phone_swipe",
        "description": "Swipe on the phone screen. Use direction for scrolling, coordinates for precise swipes.",
        "parameters": {"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "from_coordinate": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            "to_coordinate": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            "duration_ms": {"type": "integer"}, "element": {"type": "integer"},
        }},
    }},
    {"type": "function", "function": {
        "name": "phone_type",
        "description": "Type text into the focused field. Supports Unicode with hybrid backend.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Text to type (max 500 chars)"},
            "element": {"type": "integer", "description": "Tap this element first"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "phone_clear_text",
        "description": "Clear the current text field.",
        "parameters": {"type": "object", "properties": {
            "element": {"type": "integer"},
        }},
    }},
    {"type": "function", "function": {
        "name": "phone_set_text",
        "description": "Clear the field and type new text.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Text to set (max 500 chars)"},
            "element": {"type": "integer"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "phone_keyevent",
        "description": "Send a key event. Keycodes: BACK, HOME, ENTER, TAB, VOLUME_UP, VOLUME_DOWN, POWER, APP_SWITCH, DPAD_UP/DOWN/LEFT/RIGHT, DEL, MENU, SEARCH, SPACE, ESCAPE.",
        "parameters": {"type": "object", "properties": {
            "keycode": {"type": "string"},
        }, "required": ["keycode"]},
    }},
    {"type": "function", "function": {
        "name": "phone_launch_app",
        "description": "Launch an app by package name.",
        "parameters": {"type": "object", "properties": {
            "package": {"type": "string", "description": "e.g. 'com.android.settings'"},
            "activity": {"type": "string", "description": "Activity class (optional)"},
        }, "required": ["package"]},
    }},
    {"type": "function", "function": {
        "name": "phone_stop_app",
        "description": "Force-stop an app.",
        "parameters": {"type": "object", "properties": {
            "package": {"type": "string"},
        }, "required": ["package"]},
    }},
    {"type": "function", "function": {
        "name": "phone_list_apps",
        "description": "List installed third-party apps.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_current_app",
        "description": "Get the currently foreground app.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_device_info",
        "description": "Get device info (model, screen size, Android version).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_wait",
        "description": "Wait for a specified duration.",
        "parameters": {"type": "object", "properties": {
            "seconds": {"type": "number", "description": "Seconds (max 30)"},
        }},
    }},
]

_OPENAI_NAME_TO_ACTION = {t["function"]["name"]: t["function"]["name"].removeprefix("phone_") for t in OPENAI_TOOLS}


# ── Route handlers ─────────────────────────────────────────────────

async def handle_phone_action(request: Request) -> JSONResponse:
    action = request.path_params["action"]
    try:
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    except Exception:
        body = {}
    try:
        backend = _get_backend()
    except Exception as e:
        return JSONResponse({"error": f"backend unavailable: {e}"}, status_code=503)
    try:
        result = _dispatch(backend, action, body)
        return JSONResponse(result, status_code=200 if "error" not in result else 400)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("phone/%s failed", action)
        return JSONResponse({"error": f"{action} failed: {e}"}, status_code=500)


async def handle_status(request: Request) -> JSONResponse:
    try:
        backend = _get_backend()
        info = backend.device_info()
        return JSONResponse({
            "connected": True, "backend": os.environ.get("HERMES_PHONE_BACKEND", "adb"),
            "serial": info.serial, "model": info.model,
            "screen": f"{info.screen_width}x{info.screen_height}",
        })
    except Exception as e:
        return JSONResponse({"connected": False, "error": str(e)}, status_code=503)


async def handle_openai_tools(request: Request) -> JSONResponse:
    return JSONResponse(OPENAI_TOOLS)


async def handle_openai_call(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    func_name = body.get("name", "")
    arguments = body.get("arguments", {})
    if isinstance(arguments, str):
        try: arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid arguments JSON"}, status_code=400)
    action = _OPENAI_NAME_TO_ACTION.get(func_name)
    if not action:
        return JSONResponse({"error": f"unknown function: {func_name!r}"}, status_code=400)
    try:
        backend = _get_backend()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    try:
        return JSONResponse({"result": _dispatch(backend, action, arguments)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "phone-control"})


app = Starlette(
    routes=[
        Route("/health", handle_health, methods=["GET"]),
        Route("/phone/status", handle_status, methods=["GET"]),
        Route("/phone/{action}", handle_phone_action, methods=["POST"]),
        Route("/openai/tools", handle_openai_tools, methods=["GET"]),
        Route("/openai/call", handle_openai_call, methods=["POST"]),
    ],
    middleware=[Middleware(BearerAuthMiddleware)],
)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _validate_bind_host(host: str, allow_public: bool) -> None:
    if host in _LOOPBACK_HOSTS:
        return
    if not allow_public:
        sys.stderr.write(
            f"ERROR: refusing to bind to non-loopback host {host!r} without "
            f"--allow-public.\n"
            f"This server has only a bearer-token auth boundary. Exposing it "
            f"to the network is dangerous.\n"
            f"If you really mean it, pass --allow-public and ensure the "
            f"PHONE_API_TOKEN is strong and confidential.\n"
        )
        sys.exit(2)
    sys.stderr.write(
        f"WARNING: binding to {host!r} — phone control API is reachable from "
        f"the network. Token-only protection is in effect; rotate the token "
        f"immediately if leaked.\n"
    )


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Phone Control HTTP Server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PHONE_HTTP_PORT", "8080")))
    parser.add_argument("--allow-public", action="store_true",
                        help="Allow binding to non-loopback addresses (dangerous)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    _validate_bind_host(args.host, args.allow_public)

    token = _get_api_token()
    token_source = "PHONE_API_TOKEN env var" if os.environ.get("PHONE_API_TOKEN") else "auto-generated"
    sys.stderr.write(
        "\n──────────────────────────────────────────────────────────────\n"
        f"Phone Control HTTP Server — {args.host}:{args.port}\n"
        f"API token ({token_source}):\n  {token}\n"
        "Include in every request:  Authorization: Bearer <token>\n"
        "──────────────────────────────────────────────────────────────\n\n"
    )
    sys.stderr.flush()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
