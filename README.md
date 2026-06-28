# phone-mcp-server

Standalone MCP + HTTP server for controlling Android phones from **any** AI agent.

Works with Claude Desktop, Claude Code, OpenAI Codex CLI, GPT agents (via OpenAI API), Gemini, LangChain, AutoGen, CrewAI, Open Interpreter, or any HTTP client.

## How It Works

```
┌──────────────────────────────────────────────────┐
│  Any AI Agent                                    │
│                                                  │
│  Claude ──── MCP (stdio) ──┐                     │
│  Codex ──── MCP (stdio) ───┤                     │
│                            ▼                     │
│                     ┌──────────────┐             │
│                     │  MCP Server  │             │
│                     │  mcp_server  │             │
│                     └──────┬───────┘             │
│                            │                     │
│  GPT ──── HTTP ────┐       │                     │
│  Gemini ── HTTP ───┤       │                     │
│  Custom ── HTTP ───┤       │                     │
│                    ▼       ▼                     │
│              ┌─────────────────┐                 │
│              │  phone_control  │                 │
│              │  (core package) │                 │
│              └────────┬────────┘                 │
│                       │                          │
│              ADB  ────┤──── Appium (optional)    │
│                       │                          │
├───────────────────────┼──────────────────────────┤
│  Android Emulator     │                          │
└───────────────────────┴──────────────────────────┘
```

## Requirements

- Python 3.10+
- Android SDK Platform Tools (`adb` on PATH)
- A running Android emulator or device

**Optional** (for Unicode text input and WebView support):
- [Appium](https://appium.io/) (`npm install -g appium`)
- Appium Python client (`pip install Appium-Python-Client`)

## Install

```bash
git clone https://github.com/Ctrl-Creeper/phone-mcp-server.git
cd phone-mcp-server
pip install .

# With Appium support
pip install ".[appium]"
```

## Quick Start

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "phone-control": {
      "command": "python",
      "args": ["/path/to/phone-mcp-server/mcp_server.py"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add phone-control python /path/to/phone-mcp-server/mcp_server.py
```

### OpenAI Codex CLI

```bash
codex --mcp-config codex-mcp.json
```

Create `codex-mcp.json`:

```json
{
  "mcpServers": {
    "phone-control": {
      "command": "python",
      "args": ["/path/to/phone-mcp-server/mcp_server.py"]
    }
  }
}
```

### OpenAI API / GPT Agents

Start the HTTP server, then fetch the tool schema:

```bash
python http_server.py
```

```python
import requests, openai

tools = requests.get("http://localhost:8080/openai/tools").json()

response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Open Settings on the phone"}],
    tools=tools,
)

tool_call = response.choices[0].message.tool_calls[0]
result = requests.post("http://localhost:8080/openai/call", json={
    "name": tool_call.function.name,
    "arguments": tool_call.function.arguments,
}).json()
```

### Google Gemini

```python
import requests, google.generativeai as genai

tools_schema = requests.get("http://localhost:8080/openai/tools").json()

# Convert to Gemini format
gemini_tools = []
for t in tools_schema:
    f = t["function"]
    gemini_tools.append(genai.types.Tool(
        function_declarations=[genai.types.FunctionDeclaration(
            name=f["name"],
            description=f["description"],
            parameters=f["parameters"],
        )]
    ))

model = genai.GenerativeModel("gemini-2.0-flash", tools=gemini_tools)
chat = model.start_chat()
response = chat.send_message("Open the camera app")

# Execute the function call
fc = response.candidates[0].content.parts[0].function_call
result = requests.post("http://localhost:8080/openai/call", json={
    "name": fc.name,
    "arguments": dict(fc.args),
}).json()
```

### LangChain

```python
import requests
from langchain_core.tools import StructuredTool

def phone_action(action: str, **kwargs):
    return requests.post(f"http://localhost:8080/phone/{action}", json=kwargs).json()

# Or dynamically load from schema
tools_schema = requests.get("http://localhost:8080/openai/tools").json()
```

### Any HTTP Client (curl)

```bash
# Capture UI hierarchy
curl -s localhost:8080/phone/capture -d '{"mode":"hierarchy"}' | jq .

# Tap element #3
curl -s localhost:8080/phone/tap -d '{"element":3}' | jq .

# Type text
curl -s localhost:8080/phone/type -d '{"text":"hello world"}' | jq .

# Get device info
curl -s -X POST localhost:8080/phone/device_info | jq .

# Fetch OpenAI tool schema
curl -s localhost:8080/openai/tools | jq .
```

## Exposed Tools (15)

| Tool | Description |
|------|-------------|
| `phone_capture` | Capture screen (hierarchy / screenshot / both) |
| `phone_tap` | Tap by element index or coordinates |
| `phone_double_tap` | Double-tap |
| `phone_long_press` | Long-press (configurable duration) |
| `phone_swipe` | Swipe by direction or coordinates |
| `phone_type` | Type text (Unicode via Appium hybrid) |
| `phone_clear_text` | Clear text field |
| `phone_set_text` | Clear + type new text |
| `phone_keyevent` | Send key event (BACK, HOME, ENTER, etc.) |
| `phone_launch_app` | Launch app by package name |
| `phone_stop_app` | Force-stop app |
| `phone_list_apps` | List installed apps |
| `phone_current_app` | Get foreground app |
| `phone_device_info` | Device model, screen size, Android version |
| `phone_wait` | Wait N seconds |

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `HERMES_PHONE_BACKEND` | `adb`, `hybrid`, or `noop` | `adb` |
| `ANDROID_SERIAL` | Device serial (auto-detected if one device) | — |
| `APPIUM_PORT` | Appium server port (hybrid backend) | `4723` |
| `PHONE_POLICY_PATH` | Path to phone-policy.yaml | auto-search |
| `MCP_SERVER_PORT` | MCP SSE server port | `8765` |
| `PHONE_HTTP_PORT` | HTTP server port | `8080` |

## Policy Engine

The phone policy (`phone-policy.yaml`) controls what actions the agent can perform on which apps. Place it at `~/.hermes/phone-policy.yaml` or set `PHONE_POLICY_PATH`.

See the [virtual-phone-agent](https://github.com/Ctrl-Creeper/virtual-phone-agent) repo for the full policy reference and examples.

## Security

- All ADB commands use argument-list subprocess (no shell injection)
- `install_apk` and `shell` are blocked over HTTP API
- Policy engine enforces per-app action restrictions
- Phone content is untrusted data — never treated as instructions
- Input sanitization: shell metachar rejection, keycode allowlist, coordinate bounds, text length limits

## License

AGPL-3.0
