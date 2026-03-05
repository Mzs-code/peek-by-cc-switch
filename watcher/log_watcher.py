"""日志文件监控线程"""

import json
import os
import re
import threading
import time
import uuid

from .config import broadcast_event, watcher_config, watcher_config_lock
from .utils import parse_log_line
from .aggregator import SSEAggregator

_RAW_LOG_MAX = 500
_SYSTEM_REMINDER_RE = re.compile(r'<system-reminder>(.*?)</system-reminder>', re.DOTALL)

def _truncate_raw_log(raw_line: str) -> str:
    if len(raw_line) <= _RAW_LOG_MAX:
        return raw_line
    return raw_line[:_RAW_LOG_MAX] + f"... ({len(raw_line)} chars total)"


class LogWatcher(threading.Thread):
    """后台线程，tail -f 方式追踪日志文件"""

    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.current_aggregator = None
        self.current_request_id = None
        self.current_session_id = None
        self._line_number = 0

    def run(self):
        while not self._stop_event.is_set():
            try:
                self._watch_file()
            except Exception as e:
                broadcast_event({"type": "error", "message": f"Watcher error: {e}"})
                time.sleep(2)

    def _get_config(self):
        with watcher_config_lock:
            return watcher_config["log_file"], watcher_config["interval"]

    def _watch_file(self):
        log_file, interval = self._get_config()
        prev_size = 0

        # 检查文件是否存在
        if not os.path.exists(log_file):
            broadcast_event({
                "type": "error",
                "message": f"日志文件不存在: {log_file}"
            })
            time.sleep(interval)
            return

        # 从文件末尾开始（只看新增内容）
        try:
            prev_size = os.path.getsize(log_file)
        except OSError:
            prev_size = 0

        broadcast_event({
            "type": "status",
            "message": f"开始监控: {log_file} (轮询间隔: {interval}s)"
        })

        while not self._stop_event.is_set():
            # 检查配置是否变更
            with watcher_config_lock:
                if watcher_config["file_changed"].is_set():
                    watcher_config["file_changed"].clear()
                    return  # 退出当前循环，重新进入 _watch_file

            try:
                current_size = os.path.getsize(log_file)
            except OSError:
                time.sleep(0.3)
                continue

            # 文件被截断或轮转
            if current_size < prev_size:
                prev_size = 0

            if current_size > prev_size:
                try:
                    with open(log_file, "rb") as f:
                        f.seek(prev_size)
                        raw = f.read()
                        prev_size = f.tell()
                    new_data = raw.decode("utf-8", errors="replace")
                    self._process_lines(new_data)
                except OSError:
                    pass

            time.sleep(0.3)

    def _process_lines(self, data: str):
        """处理新增的日志行"""
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            self._line_number += 1
            parsed = parse_log_line(line)
            if parsed is None:
                continue

            date, time_str, level, module, message = parsed

            # 只处理 proxy 相关模块
            if ("proxy::forwarder" not in module
                    and "proxy::response_processor" not in module
                    and "proxy::handler_context" not in module):
                continue

            self._handle_proxy_line(date, time_str, module, message, line)

    def _handle_proxy_line(self, date: str, time_str: str, module: str, message: str, raw_line: str):
        """处理代理相关的日志行"""

        # 0. Session ID 声明 (来自 proxy::handler_context)
        m = re.match(r"\[[^\]]+\]\s*Session ID:\s*([\w-]+)", message)
        if m:
            self.current_session_id = m.group(1)
            return

        # 消息格式: "[Claude] >>> 请求 URL: ..." — 用 \[[^\]]+\] 锚定开头的客户端标签，
        # 防止 [.*?] 回溯扩展到 JSON 请求体内部的同名模式

        # 1. 新请求: >>> 请求 URL:
        m = re.match(r"\[[^\]]+\]\s*>>> 请求 URL:\s*(\S+)\s*\(model=([^)]+)\)", message)
        if m:
            self.current_request_id = str(uuid.uuid4())
            self.current_aggregator = SSEAggregator(self.current_request_id)
            self.current_aggregator.model = m.group(2)
            broadcast_event({
                "type": "request_start",
                "id": self.current_request_id,
                "time": time_str,
                "date": date,
                "model": m.group(2),
                "url": m.group(1),
                "session_id": self.current_session_id,
            })
            return

        # 2. 请求体: >>> 请求体内容
        m = re.match(r"\[[^\]]+\]\s*>>> 请求体内容\s*\(\d+字节\):\s*(.*)", message, re.DOTALL)
        if m and self.current_request_id:
            try:
                body = json.loads(m.group(1))

                # 广播完整请求体
                broadcast_event({
                    "type": "request_body",
                    "id": self.current_request_id,
                    "body": body,
                })

                # a. 提取顶层 system 指令
                system = body.get("system", "")
                if isinstance(system, list):
                    system = "\n".join(
                        item.get("text", "") for item in system
                        if isinstance(item, dict) and item.get("type") == "text"
                    )
                if system:
                    broadcast_event({
                        "type": "context_message",
                        "id": self.current_request_id,
                        "role": "system",
                        "content": system[:10000],
                    })

                # b. 提取 tools 列表
                tools = body.get("tools", [])
                if tools and isinstance(tools, list):
                    broadcast_event({
                        "type": "tools_list",
                        "id": self.current_request_id,
                        "tools": tools,
                    })

                # c. 遍历所有 messages
                messages = body.get("messages", [])
                for i, msg in enumerate(messages):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")

                    # 分离 system-reminder 和普通内容
                    sys_texts, regular_text = self._split_system_reminders(content)

                    # 广播 system-reminder 为独立系统提醒块
                    for st in sys_texts:
                        broadcast_event({
                            "type": "context_message",
                            "id": self.current_request_id,
                            "role": "system-reminder",
                            "content": st[:10000],
                        })

                    if not regular_text:
                        continue
                    is_last_user = (role == "user" and i == len(messages) - 1)
                    broadcast_event({
                        "type": "context_message",
                        "id": self.current_request_id,
                        "role": role,
                        "content": regular_text[:10000],
                        "is_last": is_last_user,
                    })
            except Exception as exc:
                broadcast_event({
                    "type": "parse_error",
                    "time": time_str,
                    "reason": f"请求体解析失败: {type(exc).__name__}: {exc}",
                    "raw_log": _truncate_raw_log(raw_line),
                    "line": self._line_number,
                })
            return

        # 3. SSE 事件: <<< SSE 事件:
        m = re.match(r"\[[^\]]+\]\s*<<< SSE 事件:\s*(.*)", message)
        if m and self.current_aggregator:
            json_str = m.group(1).strip()
            # 日志行尾可能有多余空格
            try:
                sse_data = json.loads(json_str)
                self.current_aggregator.feed(sse_data)
            except json.JSONDecodeError as exc:
                broadcast_event({
                    "type": "parse_error",
                    "time": time_str,
                    "reason": f"SSE JSON 解析失败: {type(exc).__name__}: {exc}",
                    "raw_log": _truncate_raw_log(raw_line),
                    "line": self._line_number,
                })
            return

        # 4. 请求完成: 记录请求日志:
        m = re.match(r"\[[^\]]+\]\s*记录请求日志:\s*(.*)", message)
        if m and self.current_request_id:
            stats_str = m.group(1)
            stats = {}
            for pair in stats_str.split(", "):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    stats[k.strip()] = v.strip()

            broadcast_event({
                "type": "request_complete",
                "id": self.current_request_id,
                "session_id": stats.get("session", ""),
                "status": int(stats.get("status", 0)),
                "latency_ms": int(stats.get("latency_ms", 0)),
                "first_token_ms": self._parse_optional_int(stats.get("first_token_ms", "0")),
                "input_tokens": int(stats.get("input", 0)),
                "output_tokens": int(stats.get("output", 0)),
                "cache_read": int(stats.get("cache_read", 0)),
                "cache_creation": int(stats.get("cache_creation", 0)),
                "model": stats.get("model", ""),
            })
            self.current_request_id = None
            self.current_aggregator = None
            return

        # 5. 请求失败: 所有 Provider 均失败
        if "FWD-002" in message and self.current_request_id:
            broadcast_event({
                "type": "request_complete",
                "id": self.current_request_id,
                "status": 502,
                "latency_ms": 0,
                "first_token_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "model": "",
            })
            self.current_request_id = None
            self.current_aggregator = None
            return

    @staticmethod
    def _parse_optional_int(s: str) -> int:
        """解析 Some(123) 或纯数字"""
        m = re.search(r"(\d+)", s)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _extract_message_text(content) -> str:
        """将消息 content（字符串或内容块数组）提取为纯文本"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    t = item.get("type", "")
                    if t == "text":
                        parts.append(item.get("text", ""))
                    elif t == "tool_use":
                        parts.append(f"[Tool Use: {item.get('name', '')}]")
                    elif t == "tool_result":
                        tr_content = item.get("content", "")
                        if isinstance(tr_content, str):
                            parts.append(f"[Tool Result]\n{tr_content}")
                        elif isinstance(tr_content, list):
                            tr_texts = [c.get("text", "") for c in tr_content if isinstance(c, dict) and c.get("type") == "text"]
                            parts.append(f"[Tool Result]\n" + "\n".join(tr_texts))
            return "\n".join(parts)
        return str(content)

    @classmethod
    def _split_system_reminders(cls, content):
        """将 content 中的 <system-reminder> 拆分为独立系统指令。

        返回 (system_texts: list[str], regular_text: str)
        """
        if isinstance(content, str):
            sys_parts = _SYSTEM_REMINDER_RE.findall(content)
            regular = _SYSTEM_REMINDER_RE.sub('', content).strip()
            return sys_parts, regular
        if isinstance(content, list):
            sys_parts = []
            regular_items = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if "<system-reminder>" in text:
                        found = _SYSTEM_REMINDER_RE.findall(text)
                        sys_parts.extend(found)
                        remaining = _SYSTEM_REMINDER_RE.sub('', text).strip()
                        if remaining:
                            regular_items.append({"type": "text", "text": remaining})
                    else:
                        regular_items.append(item)
                else:
                    regular_items.append(item)
            regular_text = cls._extract_message_text(regular_items)
            return sys_parts, regular_text
        return [], str(content)
