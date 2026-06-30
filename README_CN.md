# phone-mcp-server

[English](README.md) | 中文

独立的 MCP + HTTP 服务器，让**任何** AI Agent 都能控制 Android 手机。

支持 Claude Desktop、Claude Code、OpenAI Codex CLI、GPT（通过 OpenAI API）、Gemini、LangChain、AutoGen、CrewAI、Open Interpreter，以及任何 HTTP 客户端。

## 工作原理

```
┌──────────────────────────────────────────────────┐
│  任意 AI Agent                                   │
│                                                  │
│  Claude ──── MCP (stdio) ──┐                     │
│  Codex ──── MCP (stdio) ───┤                     │
│                            ▼                     │
│                     ┌──────────────┐             │
│                     │  MCP 服务器   │             │
│                     │  mcp_server  │             │
│                     └──────┬───────┘             │
│                            │                     │
│  GPT ──── HTTP ────┐       │                     │
│  Gemini ── HTTP ───┤       │                     │
│  自定义 ── HTTP ───┤       │                     │
│                    ▼       ▼                     │
│              ┌─────────────────┐                 │
│              │  phone_control  │                 │
│              │  (核心包)        │                 │
│              └────────┬────────┘                 │
│                       │                          │
│              ADB  ────┤──── Appium (可选)         │
│                       │                          │
├───────────────────────┼──────────────────────────┤
│  Android 模拟器        │                          │
└───────────────────────┴──────────────────────────┘
```

## 环境要求

- Python 3.10+
- Android SDK Platform Tools（`adb` 在 PATH 中）
- 正在运行的 Android 模拟器或真机

**可选**（Unicode 文本输入和 WebView 支持）：
- [Appium](https://appium.io/)（`npm install -g appium`）
- Appium Python 客户端（`pip install Appium-Python-Client`）

## 安装

```bash
git clone https://github.com/Ctrl-Creeper/phone-mcp-server.git
cd phone-mcp-server
pip install .

# 安装 Appium 支持
pip install ".[appium]"
```

## 快速开始

### Claude Desktop

添加到 `~/Library/Application Support/Claude/claude_desktop_config.json`：

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

创建 `codex-mcp.json`：

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

### OpenAI API / GPT Agent

启动 HTTP 服务器，然后获取工具 schema：

```bash
python http_server.py
```

```python
import requests, openai

tools = requests.get("http://localhost:8080/openai/tools").json()

response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "打开手机设置"}],
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

# 转换为 Gemini 格式
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
response = chat.send_message("打开相机应用")

# 执行函数调用
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

# 或从 schema 动态加载
tools_schema = requests.get("http://localhost:8080/openai/tools").json()
```

### 任意 HTTP 客户端 (curl)

```bash
# 获取 UI 层级树
curl -s localhost:8080/phone/capture -d '{"mode":"hierarchy"}' | jq .

# 点击第 3 个元素
curl -s localhost:8080/phone/tap -d '{"element":3}' | jq .

# 输入文本
curl -s localhost:8080/phone/type -d '{"text":"你好世界"}' | jq .

# 获取设备信息
curl -s -X POST localhost:8080/phone/device_info | jq .

# 获取 OpenAI 工具 schema
curl -s localhost:8080/openai/tools | jq .
```

## 提供的工具 (15 个)

| 工具 | 说明 |
|------|------|
| `phone_capture` | 捕获屏幕（层级树 / 截图 / 两者） |
| `phone_tap` | 通过元素索引或坐标点击 |
| `phone_double_tap` | 双击 |
| `phone_long_press` | 长按（可配置时长） |
| `phone_swipe` | 通过方向或坐标滑动 |
| `phone_type` | 输入文本（混合后端支持 Unicode） |
| `phone_clear_text` | 清空文本框 |
| `phone_set_text` | 清空并输入新文本 |
| `phone_keyevent` | 发送按键事件（BACK、HOME、ENTER 等） |
| `phone_launch_app` | 通过包名启动应用 |
| `phone_stop_app` | 强制停止应用 |
| `phone_list_apps` | 列出已安装应用 |
| `phone_current_app` | 获取前台应用 |
| `phone_device_info` | 设备型号、屏幕大小、Android 版本 |
| `phone_wait` | 等待 N 秒 |

## 配置

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `HERMES_PHONE_BACKEND` | `adb`、`hybrid` 或 `noop` | `adb` |
| `ANDROID_SERIAL` | 设备序列号（仅一台设备时自动检测） | — |
| `APPIUM_PORT` | Appium 服务器端口（混合后端） | `4723` |
| `PHONE_POLICY_PATH` | phone-policy.yaml 路径 | 自动搜索 |
| `MCP_SERVER_PORT` | MCP SSE 服务器端口 | `8765` |
| `PHONE_HTTP_PORT` | HTTP 服务器端口 | `8080` |

## 策略引擎

手机策略（`phone-policy.yaml`）控制 Agent 可以在哪些应用上执行哪些操作。将文件放在 `~/.hermes/phone-policy.yaml` 或通过 `PHONE_POLICY_PATH` 指定路径。

完整策略参考和示例见 [virtual-phone-agent](https://github.com/Ctrl-Creeper/virtual-phone-agent) 仓库。

## 安全

- 所有 ADB 命令使用参数列表方式调用 subprocess（无 shell 注入）
- `install_apk` 和 `shell` 在 HTTP API 中被禁止
- 策略引擎强制执行按应用的操作限制
- 手机内容是不可信数据 — 永远不会被当作指令
- 输入清洗：shell 元字符拒绝、按键码白名单、坐标边界检查、文本长度限制

## 许可证

AGPL-3.0
