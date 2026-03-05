# CCSwitchWatch — Claude Code 日志监控工具

## 需求背景

[CC Switch](https://github.com/farion1231/cc-switch) 是一个 Claude Code 代理管理工具，所有 API 请求和响应都会记录到日志文件 `~/.cc-switch/logs/cc-switch.log`。但这个日志存在两个核心问题：

1. **噪音过多** — 日志中混杂了大量无关内容（TrayIcon 事件、连接池、更新检测等），与 Claude Code 实际交互的关键信息被淹没。
2. **SSE 碎片化** — Claude API 的响应以 SSE（Server-Sent Events）流式方式记录，一条完整回复被拆成几十到几百行 `content_block_delta` 日志，无法直接阅读。

**CCSwitchWatch** 就是为了解决这两个问题而创建的：实时监控日志，过滤噪音，聚合 SSE 碎片，重建完整对话，以可交互的 Web UI 展示。

## 架构设计

零外部依赖，仅使用 Python 标准库。按功能模块拆分，前端 CSS/JS 独立文件可获得 IDE 语法支持。

```
watch_claude.py (入口)
│
├── watcher/
│   ├── config.py         → 默认配置、全局状态、broadcast_event
│   ├── utils.py          → 八进制转义解码、日志行解析
│   ├── aggregator.py     → SSEAggregator 状态机
│   ├── log_watcher.py    → LogWatcher 后台守护线程
│   └── server.py         → RequestHandler (HTTP + SSE + HTML 拼装)
│
├── static/
│   ├── style.css         → 所有 CSS（主题变量、布局、组件样式）
│   └── script.js         → 所有 JS（i18n、SSE 连接、卡片/会话管理）
│
└── templates/
    └── index.html        → HTML 骨架，{{STYLE}}/{{SCRIPT}} 占位符
```

### 核心组件

```
ThreadingHTTPServer (HTTP + SSE 服务器)
├── GET /             → 返回拼装后的完整 HTML 页面（CSS/JS 内联注入）
├── GET /events       → SSE 长连接，实时推送解析后的日志事件
├── POST /api/set-file     → 切换监控的日志文件
└── POST /api/set-interval → 修改轮询间隔

LogWatcher (后台守护线程)
├── 文件追踪: open() + seek + 轮询 (0.3s)，检测截断/轮转
├── 日志过滤: 仅处理 proxy::forwarder、proxy::response_processor 和 proxy::handler_context 模块
├── Session 追踪: 从 handler_context 提取 Session ID，关联同一 Claude Code 对话的多次请求
└── 日志解析: 正则提取请求 URL、请求体、SSE 事件、完成统计

SSEAggregator (SSE 碎片聚合状态机)
├── message_start      → 记录 model、初始 usage
├── content_block_start → 开启新块 (thinking / text / tool_use)
├── content_block_delta → 累积内容到当前块
├── content_block_stop  → 推送完整的 content_block 事件
├── message_delta       → 记录 stop_reason、最终 usage
└── message_stop        → 完成
```

### 为什么用 SSE 而不是 WebSocket

通信模式是 **服务端 → 客户端** 为主（推送日志事件），客户端 → 服务端只有 2 个操作（切换文件、修改间隔），用普通 HTTP POST 即可。SSE 是普通 HTTP + `text/event-stream`，Python 标准库直接支持，浏览器原生 `EventSource` API，无需手写 WebSocket 协议。

## 功能特性

### 日志解析

| 日志标记 | 含义 | 处理方式 |
|---------|------|---------|
| `Session ID:` | Session 声明（handler_context） | 记录当前 Session ID，关联后续请求 |
| `>>> 请求 URL:` | 新请求开始 | 提取 URL、model、时间戳，附带 session_id |
| `>>> 请求体内容` | 请求体 JSON | 提取 system 指令 + tools 工具定义 + 全部对话消息，分块广播 |
| `<<< SSE 事件:` | 流式响应碎片 | 按 type 分发到 SSE 聚合器 |
| `记录请求日志:` | 请求完成 | 提取 status、latency、tokens 统计 |
| `[FWD-002]` | 所有 Provider 失败 | 以 status=502 关闭当前请求卡片 |

### Web UI

- **会话分组（双栏布局）** — 左侧会话列表 + 右侧请求卡片。同一 Claude Code 对话（共享 Session ID）的多次请求归入同一会话，点击左侧切换查看
  - **侧边栏收起/展开** — sidebar-header 右侧 `«` 按钮可折叠侧边栏（CSS transition 平滑动画，宽度过渡到 0），折叠后左侧显示窄条 `»` 展开按钮，右侧主内容区自动撑满。折叠状态通过 `localStorage` (`cc-watch-sidebar-collapsed`) 持久化，刷新页面后自动恢复
  - 会话列表项显示模型名称、创建时间、最后更新时间和请求次数
  - 按最后更新时间倒序排列，有新请求的会话自动排到顶部
  - 首个请求到达时自动选中对应会话
  - 请求完成时根据 `记录请求日志` 中的 `session=` 字段自动修正归属，避免跨会话误分
- **请求卡片** — 每次 API 交互一张卡片，按时间倒序排列
- **分区块展示** — 系统指令（灰）、工具列表（靛蓝）、系统提醒（橙）、用户消息（蓝）、助手回复（绿/半透明）、Thinking（紫）、回复（绿）、工具调用（橙）、统计栏（灰）
- **完整日志开关** — 工具栏右侧 checkbox，默认关闭。关闭时只展示最后一条用户消息和 SSE 响应；开启后，后续新请求会完整展示系统指令、系统提醒（`<system-reminder>`）和历史对话。状态保存到 localStorage
- **完整请求体展示** — 开启完整日志后，将 Claude API 请求体中的所有内容分块展示：顶层 system prompt（📋 系统指令）、tools 工具定义列表（🛠 工具列表）、`<system-reminder>` 内容（📌 系统提醒，从用户消息中拆分）、完整对话历史、最后一条用户消息。历史消息默认折叠，最后一条用户消息默认展开
- **工具列表展示** — 从请求体 `tools` 数组提取，仅在完整日志模式下显示，默认折叠。每个工具以独立卡片展示：工具名（monospace accent 色）、description（3 行截断 + 展开/收起按钮）、input_schema（格式化 JSON + 右上角复制按钮）。支持整体复制（全部 tools JSON）和单个工具复制。展示位置在系统指令之后、系统提醒之前
- **超长日志截断** — 请求体/SSE 解析失败时，错误面板中的原始日志截断到 500 字符并附带总长度提示，防止 82KB+ 的 raw_log 通过 SSE 广播到前端
- **折叠/展开** — 卡片级别和内容块级别独立折叠，折叠时显示首行预览；`addBlock` 支持 `defaultCollapsed` 参数控制初始折叠状态
- **长内容截断** — 内容块高度超过 200px 时自动截断，底部显示渐变遮罩（颜色匹配各块类型背景），附带居中的"▾ 展开"按钮；点击展开后显示全文，按钮变为"▴ 收起"。截断按钮放在 `contentDiv` 外部（`block` 的直接子元素），避免被 `overflow: hidden` 裁掉。默认折叠的块通过 `_needsClampCheck` 标记延迟到展开时再检查
- **内容块复制** — 每个内容块展开后，左上角（对齐 header ▼ 箭头下方）显示 `⧉` 复制图标，绝对定位确保所有块类型位置一致；点击复制该块的纯文本内容（不含图标），收起时随内容区隐藏
- **JSON 报文复制** — 卡片 header 状态码右侧的"复制JSON"按钮，点击后复制该请求的完整原始报文（`{request: 请求体, response: {content: [响应块]}}`）为格式化 JSON。后端在请求体解析成功后广播 `request_body` 事件传递完整请求体，前端通过 `cardRawBodies` 和 `cardResponseBlocks` 两个全局对象收集数据
- **统计信息** — 每张卡片底部展示 input/output tokens、cache、latency、TTFT
- **错误收集面板** — 右下角常驻错误面板（z-index: 300），实时收集后端解析错误（JSON 解析失败、请求体解析异常等），每条记录包含发生时间、错误原因、原始日志行号和内容；长日志单行截断，点击可展开查看；支持一键清空
- **Toast 通知** — 页面顶部居中弹出（z-index: 400），从上方滑入，3s 后自动消失，不被错误面板遮挡
- **回到顶部** — 右下角悬浮按钮，向下滚动超过 200px 时自动显示，点击平滑滚动至顶部
- **明暗主题** — 右上角切换，保存到 localStorage
- **中英文切换** — 右上角语言按钮，所有 UI 文本支持中英双语
- **动态配置** — 前端可实时修改日志文件路径和轮询间隔

### 特殊处理

- **八进制转义解码** — Rust 日志中文件路径的中文字符以 `\345\255\246` 形式记录，自动解码回 UTF-8
- **文件追踪竞态修复** — `_watch_file` 使用二进制模式 `open("rb")` + `f.tell()` 追踪实际读取字节位置，避免 `os.path.getsize()` 与 `f.read()` 之间文件增长导致同一行被重复处理
- **防误匹配** — 使用 `re.match` + `\[[^\]]+\]` 锚定日志行开头，防止请求体 JSON 内部的文本被误识别为新请求
- **消息内容提取** — `_extract_message_text` 静态方法统一处理字符串/内容块数组两种消息格式，支持 `text`、`tool_use`、`tool_result`（含嵌套文本）类型
- **system-reminder 拆分** — `_split_system_reminders` 从用户消息中识别 `<system-reminder>` 标签，将其内容拆分为独立的系统提醒块（role=`system-reminder`），用户消息中只保留纯文本
- **异常捕获范围扩大** — 请求体解析的 except 从 `(JSONDecodeError, KeyError, IndexError)` 扩大到 `Exception`，所有异常都走截断 + 上报流程
- **失败请求处理** — 当所有 Provider 均失败（`FWD-002`）时，自动以 502 状态关闭请求卡片，避免出现永远停留在 `...` 的幽灵卡片

## 使用方式

```bash
# 基本启动（自动打开浏览器）
python3 watch_claude.py

# 指定端口
python3 watch_claude.py --port 9000

# 指定日志文件和轮询间隔
python3 watch_claude.py --log-file /path/to/log --interval 2

# 不自动打开浏览器
python3 watch_claude.py --no-browser
```

## 事件数据格式

后端通过 SSE 推送以下事件类型到前端：

```jsonc
// 新请求（附带 session_id）
{"type": "request_start", "id": "uuid", "time": "18:07:36", "model": "claude-opus-4-6", "url": "...", "session_id": "bdb1bf85-..."}

// 上下文消息（system 指令 / system-reminder / 对话历史 / 最后一条用户消息）
{"type": "context_message", "id": "uuid", "role": "system", "content": "You are Claude Code..."}
{"type": "context_message", "id": "uuid", "role": "system-reminder", "content": "The following skills are available..."}
{"type": "context_message", "id": "uuid", "role": "user", "content": "你好", "is_last": false}
{"type": "context_message", "id": "uuid", "role": "assistant", "content": "你好！...", "is_last": false}
{"type": "context_message", "id": "uuid", "role": "user", "content": "你是什么模型", "is_last": true}

// 完整请求体（用于 JSON 复制功能）
{"type": "request_body", "id": "uuid", "body": {"model": "claude-opus-4-6", "system": [...], "messages": [...], "tools": [...]}}

// 工具定义列表（仅完整日志模式下前端展示）
{"type": "tools_list", "id": "uuid", "tools": [{"name": "Bash", "description": "...", "input_schema": {...}}, ...]}

// 用户消息（已废弃，保留向后兼容）
{"type": "user_message", "id": "uuid", "content": "你是什么模型"}

// 内容块（thinking / text / tool_use）
{"type": "content_block", "id": "uuid", "block_type": "thinking", "text": "The user wants..."}
{"type": "content_block", "id": "uuid", "block_type": "text", "text": "我是 Claude..."}
{"type": "content_block", "id": "uuid", "block_type": "tool_use", "name": "Read", "tool_id": "toolu_xxx", "input": {...}}

// 请求完成统计（附带权威 session_id）
{"type": "request_complete", "id": "uuid", "session_id": "bdb1bf85-...", "status": 200, "latency_ms": 7312, "first_token_ms": 7056, "input_tokens": 3, "output_tokens": 24, "cache_read": 20349, "cache_creation": 36}

// 解析错误（请求体/SSE JSON 解析失败时上报，raw_log 截断到 500 字符）
{"type": "parse_error", "time": "18:07:36", "reason": "SSE JSON 解析失败: JSONDecodeError: ...", "raw_log": "前 500 字符... (82000 chars total)", "line": 12345}
```

## 项目结构

```
CCSwitchWatch/
├── watch_claude.py          # 入口 (main + argparse)
├── watcher/
│   ├── __init__.py          # 包导出
│   ├── config.py            # 默认配置、全局状态、broadcast
│   ├── utils.py             # 八进制转义解码、日志行解析
│   ├── aggregator.py        # SSEAggregator 类
│   ├── log_watcher.py       # LogWatcher 线程类
│   └── server.py            # RequestHandler (HTTP + SSE + HTML 拼装)
├── static/
│   ├── style.css            # 所有 CSS
│   └── script.js            # 所有 JS
├── templates/
│   └── index.html           # HTML 骨架 + {{STYLE}}/{{SCRIPT}} 占位符
├── test_changes.py          # 单元测试（48 个用例）
├── testReadme.md            # 测试用例详细说明
└── README.md
```

## 测试

```bash
python3 test_changes.py
```

共 48 个用例，详见 [testReadme.md](testReadme.md)。
