#!/usr/bin/env python3
"""测试本次修改的核心逻辑"""

import json
import sys
import os

# 确保可以 import watcher 包
sys.path.insert(0, os.path.dirname(__file__))

from watcher.log_watcher import _truncate_raw_log, _RAW_LOG_MAX, LogWatcher

# ─── 收集 broadcast 事件 ───
captured_events = []
import watcher.config as config
_original_broadcast = config.broadcast_event
def mock_broadcast(event_data):
    captured_events.append(event_data)
config.broadcast_event = mock_broadcast
# 同时 patch log_watcher 模块中已导入的引用
import watcher.log_watcher as lw_mod
lw_mod.broadcast_event = mock_broadcast
import watcher.aggregator as agg_mod
agg_mod.broadcast_event = mock_broadcast

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name}")
        failed += 1

# ═══════════════════════════════════════════════════════════════
print("1. 测试 _truncate_raw_log")
# ═══════════════════════════════════════════════════════════════

short = "a" * 500
test("短行不截断", _truncate_raw_log(short) == short)

long_line = "x" * 82000
result = _truncate_raw_log(long_line)
test("长行截断到 500+后缀", result.startswith("x" * 500 + "..."))
test("包含总长度提示", "82000 chars total" in result)
test("截断结果长度合理", len(result) < 600)

# ═══════════════════════════════════════════════════════════════
print("\n2. 测试 _extract_message_text")
# ═══════════════════════════════════════════════════════════════

ext = LogWatcher._extract_message_text

# 纯字符串
test("纯字符串直接返回", ext("hello") == "hello")

# text 块数组
test("text 块提取", ext([{"type": "text", "text": "aaa"}, {"type": "text", "text": "bbb"}]) == "aaa\nbbb")

# tool_use
test("tool_use 提取", "[Tool Use: Read]" in ext([{"type": "tool_use", "name": "Read", "input": {}}]))

# tool_result (字符串 content)
test("tool_result 字符串", "[Tool Result]\nfile content" in ext([{"type": "tool_result", "content": "file content"}]))

# tool_result (数组 content)
test("tool_result 数组", "inner text" in ext([{"type": "tool_result", "content": [{"type": "text", "text": "inner text"}]}]))

# 混合
mixed = [
    {"type": "text", "text": "请帮我读文件"},
    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
]
result = ext(mixed)
test("混合内容", "请帮我读文件" in result and "[Tool Use: Bash]" in result)

# 空列表
test("空列表返回空字符串", ext([]) == "")

# 非字符串非列表
test("其他类型转字符串", ext(12345) == "12345")

# ═══════════════════════════════════════════════════════════════
print("\n3. 测试请求体解析 → context_message 事件")
# ═══════════════════════════════════════════════════════════════

watcher = LogWatcher()
watcher._get_client_state("Claude")["current_request_id"] = "test-req-001"
watcher._line_number = 42

# 构造请求体
request_body = {
    "model": "claude-sonnet-4-20250514",
    "system": [
        {"type": "text", "text": "You are Claude Code, an AI assistant."},
        {"type": "text", "text": "CLAUDE.md content here..."},
    ],
    "messages": [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
        {"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>Current date is 2026-03-03</system-reminder>"},
            {"type": "text", "text": "你是什么模型"},
        ]},
    ],
}

body_json = json.dumps(request_body, ensure_ascii=False)
log_line = f"[2026-03-03][10:30:00][INFO][cc_switch::proxy::forwarder] [Claude] >>> 请求体内容 ({len(body_json)}字节): {body_json}"

captured_events.clear()
from watcher.utils import parse_log_line
parsed = parse_log_line(log_line)
assert parsed is not None, "日志行解析失败"
date, time_str, level, module, message = parsed
watcher._handle_proxy_line(date, time_str, module, message, log_line)

test("产生了事件", len(captured_events) > 0)

# 检查事件类型
types = [e["type"] for e in captured_events]
test("包含 context_message 事件", "context_message" in types)
test("无 user_message 事件（旧类型）", "user_message" not in types)

# 检查 system 指令（顶层 system）
system_evts = [e for e in captured_events if e.get("role") == "system"]
test("有 1 条顶层 system 事件", len(system_evts) == 1)
test("顶层 system 包含 CLAUDE.md", "CLAUDE.md" in system_evts[0]["content"])

# 检查 system-reminder 被拆为独立块（role="system-reminder"）
reminder_evts = [e for e in captured_events if e.get("role") == "system-reminder"]
test("system-reminder 拆为独立块", len(reminder_evts) == 1)
test("system-reminder 内容正确", "Current date is 2026-03-03" in reminder_evts[0]["content"])

# 检查消息遍历
user_evts = [e for e in captured_events if e.get("role") == "user"]
assistant_evts = [e for e in captured_events if e.get("role") == "assistant"]
test("有 2 条 user 消息", len(user_evts) == 2)
test("有 1 条 assistant 消息", len(assistant_evts) == 1)

# 检查 is_last 标记
test("第一条 user 不是 is_last", user_evts[0].get("is_last") == False)
test("最后一条 user 是 is_last", user_evts[1].get("is_last") == True)

# 检查 system-reminder 不再混在用户消息中
test("用户消息不含 system-reminder 标签", "<system-reminder>" not in user_evts[1]["content"])
test("最后一条用户消息只有纯文本", user_evts[1]["content"] == "你是什么模型")

# 检查内容截断到 10000
test("请求 ID 正确", all(e["id"] == "test-req-001" for e in captured_events))

# ═══════════════════════════════════════════════════════════════
print("\n4. 测试请求体解析失败 → 截断 raw_log")
# ═══════════════════════════════════════════════════════════════

watcher._get_client_state("Claude")["current_request_id"] = "test-req-002"
watcher._line_number = 99

# 构造一个超长的非法 JSON
bad_json = "{" + "x" * 82000
bad_log = f"[2026-03-03][10:31:00][INFO][cc_switch::proxy::forwarder] [Claude] >>> 请求体内容 (82001字节): {bad_json}"

captured_events.clear()
parsed = parse_log_line(bad_log)
assert parsed is not None
date, time_str, level, module, message = parsed
watcher._handle_proxy_line(date, time_str, module, message, bad_log)

test("产生 parse_error 事件", len(captured_events) == 1)
err_evt = captured_events[0]
test("类型是 parse_error", err_evt["type"] == "parse_error")
test("原因包含异常类型", "JSONDecodeError" in err_evt["reason"])
test("raw_log 被截断", len(err_evt["raw_log"]) < 600)
test("raw_log 包含总长度", "chars total" in err_evt["raw_log"])

# ═══════════════════════════════════════════════════════════════
print("\n5. 测试 SSE 解析失败 → 截断 raw_log")
# ═══════════════════════════════════════════════════════════════

from watcher.aggregator import SSEAggregator
watcher._get_client_state("Claude")["current_request_id"] = "test-req-003"
watcher._get_client_state("Claude")["current_aggregator"] = SSEAggregator("test-req-003")
watcher._line_number = 150

bad_sse = "not-json " + "y" * 80000
bad_sse_log = f"[2026-03-03][10:32:00][INFO][cc_switch::proxy::response_processor] [Claude] <<< SSE 事件: {bad_sse}"

captured_events.clear()
parsed = parse_log_line(bad_sse_log)
assert parsed is not None
date, time_str, level, module, message = parsed
watcher._handle_proxy_line(date, time_str, module, message, bad_sse_log)

test("SSE 解析失败产生 parse_error", len(captured_events) == 1)
sse_err = captured_events[0]
test("SSE 错误原因正确", "SSE JSON 解析失败" in sse_err["reason"])
test("SSE raw_log 被截断", len(sse_err["raw_log"]) < 600)

# ═══════════════════════════════════════════════════════════════
print("\n6. 测试 system 为字符串（而非数组）的情况")
# ═══════════════════════════════════════════════════════════════

watcher._get_client_state("Claude")["current_request_id"] = "test-req-004"
body_str_system = {
    "system": "You are a helpful assistant.",
    "messages": [{"role": "user", "content": "hi"}],
}
body_json2 = json.dumps(body_str_system)
log_line2 = f"[2026-03-03][10:33:00][INFO][cc_switch::proxy::forwarder] [Claude] >>> 请求体内容 ({len(body_json2)}字节): {body_json2}"

captured_events.clear()
parsed = parse_log_line(log_line2)
date, time_str, level, module, message = parsed
watcher._handle_proxy_line(date, time_str, module, message, log_line2)

sys_evts = [e for e in captured_events if e.get("role") == "system"]
test("字符串 system 也能提取", len(sys_evts) == 1)
test("字符串 system 内容正确", "helpful assistant" in sys_evts[0]["content"])

# ═══════════════════════════════════════════════════════════════
print("\n7. 测试 _split_system_reminders")
# ═══════════════════════════════════════════════════════════════

split = LogWatcher._split_system_reminders

# 纯字符串，无 system-reminder
sys_parts, regular = split("hello world")
test("无标签：sys_parts 为空", sys_parts == [])
test("无标签：regular 原样返回", regular == "hello world")

# 纯字符串，包含 system-reminder
sys_parts, regular = split("before <system-reminder>date info</system-reminder> after")
test("字符串标签：提取内容", sys_parts == ["date info"])
test("字符串标签：剩余文本", regular == "before  after")

# 数组格式，system-reminder 独占一个块
content_list = [
    {"type": "text", "text": "<system-reminder>reminder content</system-reminder>"},
    {"type": "text", "text": "用户实际输入"},
]
sys_parts, regular = split(content_list)
test("数组独占块：提取内容", sys_parts == ["reminder content"])
test("数组独占块：剩余只有用户文本", regular == "用户实际输入")

# 数组格式，同一块中混合标签和文本
content_mixed = [
    {"type": "text", "text": "prefix <system-reminder>mixed</system-reminder> suffix"},
]
sys_parts, regular = split(content_mixed)
test("混合块：提取内容", sys_parts == ["mixed"])
test("混合块：剩余保留前后文本", "prefix" in regular and "suffix" in regular)

# 数组格式，多个 system-reminder
content_multi = [
    {"type": "text", "text": "<system-reminder>first</system-reminder>"},
    {"type": "text", "text": "<system-reminder>second</system-reminder>"},
    {"type": "text", "text": "normal"},
]
sys_parts, regular = split(content_multi)
test("多标签：提取全部", sys_parts == ["first", "second"])
test("多标签：剩余正常文本", regular == "normal")

# 数组格式，无 system-reminder
content_clean = [
    {"type": "text", "text": "aaa"},
    {"type": "text", "text": "bbb"},
]
sys_parts, regular = split(content_clean)
test("无标签数组：sys_parts 为空", sys_parts == [])
test("无标签数组：文本正常拼接", regular == "aaa\nbbb")

# ═══════════════════════════════════════════════════════════════
print("\n8. 测试 Codex 请求体解析")
# ═══════════════════════════════════════════════════════════════

watcher._get_client_state("Codex")["current_request_id"] = "test-req-005"
watcher._line_number = 180

codex_body = {
    "instructions": "You are OpenCode, the best coding agent on the planet.",
    "input": [
        {
            "role": "developer",
            "type": "message",
            "content": [
                {"type": "input_text", "text": "<permissions instructions>workspace-write</permissions instructions>"},
            ],
        },
        {
            "role": "user",
            "type": "message",
            "content": [
                {"type": "input_text", "text": "你是什么模型"},
            ],
        },
    ],
    "tools": [
        {
            "type": "function",
            "name": "exec_command",
            "description": "Runs a command in a PTY.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"},
                },
            },
        },
        {
            "type": "web_search",
        },
    ],
}

codex_body_json = json.dumps(codex_body, ensure_ascii=False)
codex_log_line = f"[2026-03-18][15:09:52][DEBUG][cc_switch::proxy::forwarder] [Codex] >>> 请求体内容 ({len(codex_body_json)}字节): {codex_body_json}"

captured_events.clear()
parsed = parse_log_line(codex_log_line)
assert parsed is not None
date, time_str, level, module, message = parsed
watcher._handle_proxy_line(date, time_str, module, message, codex_log_line)

system_evts = [e for e in captured_events if e.get("role") == "system"]
reminder_evts = [e for e in captured_events if e.get("role") == "system-reminder"]
user_evts = [e for e in captured_events if e.get("role") == "user"]
tools_evts = [e for e in captured_events if e.get("type") == "tools_list"]

test("Codex instructions 被提取为 system", len(system_evts) == 1 and "OpenCode" in system_evts[0]["content"])
test("Codex developer 被提取为 system-reminder", len(reminder_evts) == 1 and "workspace-write" in reminder_evts[0]["content"])
test("Codex user 输入被提取", len(user_evts) == 1 and user_evts[0]["content"] == "你是什么模型")
test("Codex 最后一条 user 标记正确", user_evts[0].get("is_last") == True)
test("Codex tools_list 已广播", len(tools_evts) == 1)
test("Codex function tool parameters 映射为 input_schema", "input_schema" in tools_evts[0]["tools"][0])
test("Codex 无名工具回退到 type 作为名称", tools_evts[0]["tools"][1]["name"] == "web_search")

# ═══════════════════════════════════════════════════════════════
print("\n9. 测试 Codex SSE 聚合")
# ═══════════════════════════════════════════════════════════════

captured_events.clear()
aggregator = agg_mod.SSEAggregator("test-req-006")

aggregator.feed({
    "type": "response.created",
    "response": {
        "id": "resp_123",
        "model": "gpt-5.4",
        "status": "in_progress",
    },
})
aggregator.feed({
    "type": "response.output_item.added",
    "item": {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [],
    },
    "output_index": 0,
})
aggregator.feed({
    "type": "response.content_part.added",
    "item_id": "msg_1",
    "content_index": 0,
    "part": {
        "type": "output_text",
        "text": "",
    },
})
aggregator.feed({
    "type": "response.output_text.delta",
    "item_id": "msg_1",
    "content_index": 0,
    "delta": "我是",
})
aggregator.feed({
    "type": "response.output_text.delta",
    "item_id": "msg_1",
    "content_index": 0,
    "delta": " Codex",
})
aggregator.feed({
    "type": "response.output_item.done",
    "item": {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [],
        "status": "completed",
    },
    "output_index": 0,
})

aggregator.feed({
    "type": "response.output_item.added",
    "item": {
        "id": "fc_1",
        "type": "function_call",
        "name": "exec_command",
        "call_id": "call_123",
        "arguments": "",
    },
    "output_index": 1,
})
aggregator.feed({
    "type": "response.function_call_arguments.delta",
    "item_id": "fc_1",
    "delta": "{\"cmd\":\"pwd\"}",
})
aggregator.feed({
    "type": "response.output_item.done",
    "item": {
        "id": "fc_1",
        "type": "function_call",
        "name": "exec_command",
        "call_id": "call_123",
        "arguments": "{\"cmd\":\"pwd\"}",
        "status": "completed",
    },
    "output_index": 1,
})

aggregator.feed({
    "type": "response.output_item.added",
    "item": {
        "id": "rs_1",
        "type": "reasoning",
        "encrypted_content": "secret",
    },
    "output_index": 2,
})
aggregator.feed({
    "type": "response.output_item.done",
    "item": {
        "id": "rs_1",
        "type": "reasoning",
        "encrypted_content": "secret",
    },
    "output_index": 2,
})

text_blocks = [e for e in captured_events if e.get("type") == "content_block" and e.get("block_type") == "text"]
tool_blocks = [e for e in captured_events if e.get("type") == "content_block" and e.get("block_type") == "tool_use"]
reasoning_blocks = [e for e in captured_events if e.get("type") == "content_block" and e.get("block_type") == "thinking"]

test("Codex 文本 delta 能聚合成最终回复", len(text_blocks) == 1 and text_blocks[0]["text"] == "我是 Codex")
test("Codex function_call 能转为 tool_use", len(tool_blocks) == 1 and tool_blocks[0]["name"] == "exec_command")
test("Codex function_call 参数能解析 JSON", tool_blocks[0]["input"] == {"cmd": "pwd"})
test("Codex reasoning 默认不展示", len(reasoning_blocks) == 0)

# ═══════════════════════════════════════════════════════════════
print("\n10. 测试 Claude / Codex 状态隔离")
# ═══════════════════════════════════════════════════════════════

watcher = LogWatcher()
captured_events.clear()

lines = [
    "[2026-03-18][15:30:00][DEBUG][cc_switch_lib::proxy::handler_context] [Claude] Session ID: claude_session_1",
    "[2026-03-18][15:30:00][INFO][cc_switch_lib::proxy::forwarder] [Claude] >>> 请求 URL: https://api.anthropic.com/v1/messages (model=claude-sonnet-4-20250514)",
    "[2026-03-18][15:30:01][DEBUG][cc_switch_lib::proxy::handler_context] [Codex] Session ID: codex_session_1",
    "[2026-03-18][15:30:01][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][15:30:02][DEBUG][cc_switch_lib::proxy::response_processor] [Claude] <<< SSE 事件: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}",
    "[2026-03-18][15:30:02][DEBUG][cc_switch_lib::proxy::response_processor] [Claude] <<< SSE 事件: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Claude reply\"}}",
    "[2026-03-18][15:30:02][DEBUG][cc_switch_lib::proxy::response_processor] [Claude] <<< SSE 事件: {\"type\":\"content_block_stop\",\"index\":0}",
    "[2026-03-18][15:30:03][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_item.added\",\"item\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[]},\"output_index\":0}",
    "[2026-03-18][15:30:03][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.content_part.added\",\"item_id\":\"msg_1\",\"content_index\":0,\"part\":{\"type\":\"output_text\",\"text\":\"\"},\"output_index\":0}",
    "[2026-03-18][15:30:03][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_text.delta\",\"item_id\":\"msg_1\",\"content_index\":0,\"delta\":\"Codex reply\",\"output_index\":0}",
    "[2026-03-18][15:30:03][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_item.done\",\"item\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[],\"status\":\"completed\"},\"output_index\":0}",
]

for line in lines:
    parsed = parse_log_line(line)
    assert parsed is not None
    date, time_str, level, module, message = parsed
    watcher._handle_proxy_line(date, time_str, module, message, line)

request_start_events = [e for e in captured_events if e.get("type") == "request_start"]
content_blocks = [e for e in captured_events if e.get("type") == "content_block"]

test("Claude / Codex 各自产生 request_start", len(request_start_events) == 2)
test("Claude session 独立", request_start_events[0]["session_id"] == "claude_session_1")
test("Codex session 独立", request_start_events[1]["session_id"] == "codex_session_1")
test("Claude SSE 不会被 Codex 覆盖", any(e.get("text") == "Claude reply" for e in content_blocks))
test("Codex SSE 不会被 Claude 覆盖", any(e.get("text") == "Codex reply" for e in content_blocks))

# ═══════════════════════════════════════════════════════════════
print("\n11. 测试 client tag 大小写归一化 + 同 client 请求队列")
# ═══════════════════════════════════════════════════════════════

watcher = LogWatcher()
captured_events.clear()

lines = [
    "[2026-03-18][15:40:00][DEBUG][cc_switch_lib::proxy::handler_context] [Codex] Session ID: codex_session_2",
    "[2026-03-18][15:40:00][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][15:40:01][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][15:40:02][DEBUG][cc_switch_lib::proxy::response_processor] [codex] 记录请求日志: id=req_a, provider=p, model=gpt-5.4, streaming=true, status=200, latency_ms=10, first_token_ms=Some(2), session=codex_session_2, input=1, output=2, cache_read=0, cache_creation=0",
    "[2026-03-18][15:40:03][DEBUG][cc_switch_lib::proxy::response_processor] [codex] 记录请求日志: id=req_b, provider=p, model=gpt-5.4, streaming=true, status=200, latency_ms=11, first_token_ms=Some(3), session=codex_session_2, input=3, output=4, cache_read=0, cache_creation=0",
]

for line in lines:
    parsed = parse_log_line(line)
    assert parsed is not None
    date, time_str, level, module, message = parsed
    watcher._handle_proxy_line(date, time_str, module, message, line)

request_start_events = [e for e in captured_events if e.get("type") == "request_start"]
request_complete_events = [e for e in captured_events if e.get("type") == "request_complete"]

test("大小写不同的 codex 完成行仍能命中请求", len(request_complete_events) == 2)
test("同 client 并发开始后，完成顺序按队列出队", request_complete_events[0]["id"] == request_start_events[0]["id"])
test("第二个完成事件对应第二个请求", request_complete_events[1]["id"] == request_start_events[1]["id"])

# ═══════════════════════════════════════════════════════════════
print("\n12. 测试第二个 Codex 请求在 response.created 前先收到输出事件")
# ═══════════════════════════════════════════════════════════════

watcher = LogWatcher()
captured_events.clear()

lines = [
    "[2026-03-18][15:50:00][DEBUG][cc_switch_lib::proxy::handler_context] [Codex] Session ID: codex_session_3",
    "[2026-03-18][15:50:00][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][15:50:00][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.created\",\"response\":{\"id\":\"resp_1\",\"model\":\"gpt-5.4\"}}",
    "[2026-03-18][15:50:00][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_item.added\",\"item\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[]},\"output_index\":0}",
    "[2026-03-18][15:50:00][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.content_part.added\",\"item_id\":\"msg_1\",\"content_index\":0,\"part\":{\"type\":\"output_text\",\"text\":\"\"},\"output_index\":0}",
    "[2026-03-18][15:50:00][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_text.delta\",\"item_id\":\"msg_1\",\"content_index\":0,\"delta\":\"first reply\",\"output_index\":0}",
    "[2026-03-18][15:50:00][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_item.done\",\"item\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[],\"status\":\"completed\"},\"output_index\":0}",
    "[2026-03-18][15:50:01][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][15:50:01][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_item.added\",\"item\":{\"id\":\"msg_2\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[]},\"output_index\":0}",
    "[2026-03-18][15:50:01][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.content_part.added\",\"item_id\":\"msg_2\",\"content_index\":0,\"part\":{\"type\":\"output_text\",\"text\":\"\"},\"output_index\":0}",
    "[2026-03-18][15:50:01][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_text.delta\",\"item_id\":\"msg_2\",\"content_index\":0,\"delta\":\"second reply\",\"output_index\":0}",
    "[2026-03-18][15:50:01][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_item.done\",\"item\":{\"id\":\"msg_2\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[],\"status\":\"completed\"},\"output_index\":0}",
]

for line in lines:
    parsed = parse_log_line(line)
    assert parsed is not None
    date, time_str, level, module, message = parsed
    watcher._handle_proxy_line(date, time_str, module, message, line)

request_start_events = [e for e in captured_events if e.get("type") == "request_start"]
content_blocks = [e for e in captured_events if e.get("type") == "content_block" and e.get("block_type") == "text"]

test("两个 Codex 请求都生成卡片", len(request_start_events) == 2)
test("第一条回复仍归属第一张卡", any(e.get("id") == request_start_events[0]["id"] and e.get("text") == "first reply" for e in content_blocks))
test("第二条回复不会落到第一张卡", any(e.get("id") == request_start_events[1]["id"] and e.get("text") == "second reply" for e in content_blocks))

# ═══════════════════════════════════════════════════════════════
print("\n13. 测试同 client 跨 session 乱序完成不会串会话")
# ═══════════════════════════════════════════════════════════════

watcher = LogWatcher()
captured_events.clear()

lines = [
    "[2026-03-18][16:00:00][DEBUG][cc_switch_lib::proxy::handler_context] [Codex] Session ID: codex_session_a",
    "[2026-03-18][16:00:00][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][16:00:01][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][16:00:02][DEBUG][cc_switch_lib::proxy::handler_context] [Codex] Session ID: codex_session_b",
    "[2026-03-18][16:00:02][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][16:00:03][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] 记录请求日志: id=req_b1, provider=p, model=gpt-5.4, streaming=true, status=200, latency_ms=10, first_token_ms=Some(2), session=codex_session_b, input=1, output=2, cache_read=0, cache_creation=0",
    "[2026-03-18][16:00:04][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] 记录请求日志: id=req_a1, provider=p, model=gpt-5.4, streaming=true, status=200, latency_ms=11, first_token_ms=Some(3), session=codex_session_a, input=3, output=4, cache_read=0, cache_creation=0",
    "[2026-03-18][16:00:05][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] 记录请求日志: id=req_a2, provider=p, model=gpt-5.4, streaming=true, status=200, latency_ms=12, first_token_ms=Some(4), session=codex_session_a, input=5, output=6, cache_read=0, cache_creation=0",
]

for line in lines:
    parsed = parse_log_line(line)
    assert parsed is not None
    date, time_str, level, module, message = parsed
    watcher._handle_proxy_line(date, time_str, module, message, line)

request_start_events = [e for e in captured_events if e.get("type") == "request_start"]
request_complete_events = [e for e in captured_events if e.get("type") == "request_complete"]

session_a_request_ids = [e["id"] for e in request_start_events if e.get("session_id") == "codex_session_a"]
session_b_request_ids = [e["id"] for e in request_start_events if e.get("session_id") == "codex_session_b"]

test("session_a 有两次请求", len(session_a_request_ids) == 2)
test("session_b 有一次请求", len(session_b_request_ids) == 1)
test("session_b 的完成不会错误绑定到 session_a 请求", any(e.get("id") == session_b_request_ids[0] and e.get("session_id") == "codex_session_b" for e in request_complete_events))
test("session_a 的两个完成仍归属各自请求", sum(1 for e in request_complete_events if e.get("id") in session_a_request_ids and e.get("session_id") == "codex_session_a") == 2)

# ═══════════════════════════════════════════════════════════════
print("\n14. 测试跨 session 交错时请求体和首个输出不会绑到新会话")
# ═══════════════════════════════════════════════════════════════

watcher = LogWatcher()
captured_events.clear()

body_a2 = json.dumps({
    "input": [
        {
            "role": "user",
            "type": "message",
            "content": [{"type": "input_text", "text": "session a second"}],
        },
    ],
}, ensure_ascii=False)

body_a1 = json.dumps({
    "input": [
        {
            "role": "user",
            "type": "message",
            "content": [{"type": "input_text", "text": "session a first"}],
        },
    ],
}, ensure_ascii=False)

lines = [
    "[2026-03-18][16:10:00][DEBUG][cc_switch_lib::proxy::handler_context] [Codex] Session ID: codex_session_a",
    "[2026-03-18][16:10:00][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    f"[2026-03-18][16:10:00][DEBUG][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求体内容 ({len(body_a1)}字节): {body_a1}",
    "[2026-03-18][16:10:00][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] 记录请求日志: id=req_a1, provider=p, model=gpt-5.4, streaming=true, status=200, latency_ms=9, first_token_ms=Some(2), session=codex_session_a, input=1, output=1, cache_read=0, cache_creation=0",
    "[2026-03-18][16:10:01][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    "[2026-03-18][16:10:02][DEBUG][cc_switch_lib::proxy::handler_context] [Codex] Session ID: codex_session_b",
    "[2026-03-18][16:10:02][INFO][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求 URL: https://www.packyapi.com/v1/responses (model=gpt-5.4)",
    f"[2026-03-18][16:10:03][DEBUG][cc_switch_lib::proxy::forwarder] [Codex] >>> 请求体内容 ({len(body_a2)}字节): {body_a2}",
    "[2026-03-18][16:10:04][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_item.added\",\"item\":{\"id\":\"msg_a2\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[]},\"output_index\":0}",
    "[2026-03-18][16:10:04][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.content_part.added\",\"item_id\":\"msg_a2\",\"content_index\":0,\"part\":{\"type\":\"output_text\",\"text\":\"\"},\"output_index\":0}",
    "[2026-03-18][16:10:04][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_text.delta\",\"item_id\":\"msg_a2\",\"content_index\":0,\"delta\":\"reply a2\",\"output_index\":0}",
    "[2026-03-18][16:10:04][DEBUG][cc_switch_lib::proxy::response_processor] [Codex] <<< SSE 事件: {\"type\":\"response.output_item.done\",\"item\":{\"id\":\"msg_a2\",\"type\":\"message\",\"role\":\"assistant\",\"content\":[],\"status\":\"completed\"},\"output_index\":0}",
]

for line in lines:
    parsed = parse_log_line(line)
    assert parsed is not None
    date, time_str, level, module, message = parsed
    watcher._handle_proxy_line(date, time_str, module, message, line)

request_start_events = [e for e in captured_events if e.get("type") == "request_start"]
request_body_events = [e for e in captured_events if e.get("type") == "request_body"]
context_events = [e for e in captured_events if e.get("type") == "context_message" and e.get("role") == "user"]
content_blocks = [e for e in captured_events if e.get("type") == "content_block" and e.get("block_type") == "text"]

session_a_request_ids = [e["id"] for e in request_start_events if e.get("session_id") == "codex_session_a"]
session_b_request_ids = [e["id"] for e in request_start_events if e.get("session_id") == "codex_session_b"]
session_a_second_request_id = session_a_request_ids[1]

test("第二个 session_a 请求体不会绑到 session_b", any(e.get("id") == session_a_second_request_id for e in request_body_events) and all(e.get("id") != session_b_request_ids[0] for e in request_body_events))
test("第二个 session_a 用户问题不会显示到 session_b", any(e.get("id") == session_a_second_request_id and e.get("content") == "session a second" for e in context_events))
test("第二个 session_a 首个输出不会显示到 session_b", any(e.get("id") == session_a_second_request_id and e.get("text") == "reply a2" for e in content_blocks))

# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 50)
print(f"结果: {passed} 通过, {failed} 失败")
if failed:
    sys.exit(1)
else:
    print("全部测试通过! ✅")
