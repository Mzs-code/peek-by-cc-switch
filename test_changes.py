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
watcher.current_request_id = "test-req-001"
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

watcher.current_request_id = "test-req-002"
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
watcher.current_request_id = "test-req-003"
watcher.current_aggregator = SSEAggregator("test-req-003")
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

watcher.current_request_id = "test-req-004"
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
print("\n" + "═" * 50)
print(f"结果: {passed} 通过, {failed} 失败")
if failed:
    sys.exit(1)
else:
    print("全部测试通过! ✅")
