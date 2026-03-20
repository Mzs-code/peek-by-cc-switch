"""日志文件监控线程"""

from collections import deque
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
        self.client_states = {}
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
        client, payload = self._split_client_tag(message)
        state = self._get_client_state(client)

        # 0. Session ID 声明 (来自 proxy::handler_context)
        m = re.match(r"Session ID:\s*([\w-]+)", payload)
        if m:
            state["current_session_id"] = m.group(1)
            return

        # 1. 新请求: >>> 请求 URL:
        m = re.match(r">>> 请求 URL:\s*(\S+)\s*\(model=([^)]+)\)", payload)
        if m:
            request_id = str(uuid.uuid4())
            aggregator = SSEAggregator(request_id)
            aggregator.model = m.group(2)
            request = {
                "id": request_id,
                "aggregator": aggregator,
                "response_id": None,
                "item_ids": set(),
                "body_received": False,
                "session_id": state["current_session_id"],
            }
            state["request_queue"].append(request)
            self._sync_legacy_state(state)
            broadcast_event({
                "type": "request_start",
                "id": request_id,
                "time": time_str,
                "date": date,
                "model": m.group(2),
                "url": m.group(1),
                "session_id": state["current_session_id"],
            })
            return

        # 2. 请求体: >>> 请求体内容
        m = re.match(r">>> 请求体内容\s*\(\d+字节\):\s*(.*)", payload, re.DOTALL)
        request = self._oldest_request_without_body(state) or self._latest_request(state)
        if m and request:
            try:
                body = json.loads(m.group(1))
                request["body_received"] = True

                # 广播完整请求体
                broadcast_event({
                    "type": "request_body",
                    "id": request["id"],
                    "body": body,
                })

                self._broadcast_request_context(body, request["id"])

                tools = self._normalize_tools(body.get("tools", []))
                if tools:
                    broadcast_event({
                        "type": "tools_list",
                        "id": request["id"],
                        "tools": tools,
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
        m = re.match(r"<<< SSE 事件:\s*(.*)", payload)
        if m:
            json_str = m.group(1).strip()
            # 日志行尾可能有多余空格
            try:
                sse_data = json.loads(json_str)
                request = self._resolve_request_for_sse(state, sse_data)
                if request and request.get("aggregator"):
                    request["aggregator"].feed(sse_data)
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
        m = re.match(r"记录请求日志:\s*(.*)", payload)
        if m:
            stats_str = m.group(1)
            stats = {}
            for pair in stats_str.split(", "):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    stats[k.strip()] = v.strip()

            request = self._pop_oldest_request(state, stats.get("session", ""))
            if not request:
                return

            broadcast_event({
                "type": "request_complete",
                "id": request["id"],
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
            return

        # 5. 请求失败: 所有 Provider 均失败
        if "FWD-002" in payload:
            request = self._pop_oldest_request(state)
            if not request:
                return
            broadcast_event({
                "type": "request_complete",
                "id": request["id"],
                "status": 502,
                "latency_ms": 0,
                "first_token_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "model": "",
            })
            return

    @staticmethod
    def _parse_optional_int(s: str) -> int:
        """解析 Some(123) 或纯数字"""
        m = re.search(r"(\d+)", s)
        return int(m.group(1)) if m else 0

    def _broadcast_request_context(self, body: dict, request_id: str):
        system_text = self._extract_system_text(body)
        if system_text:
            broadcast_event({
                "type": "context_message",
                "id": request_id,
                "role": "system",
                "content": system_text[:10000],
            })

        messages = body.get("messages")
        if isinstance(messages, list):
            self._broadcast_claude_messages(request_id, messages)

        input_items = body.get("input")
        if isinstance(input_items, list):
            self._broadcast_openai_input_messages(request_id, input_items)

    @staticmethod
    def _extract_system_text(body: dict) -> str:
        parts = []

        instructions = body.get("instructions", "")
        if instructions:
            parts.append(LogWatcher._extract_message_text(instructions))

        system = body.get("system", "")
        if isinstance(system, list):
            system = "\n".join(
                item.get("text", "") for item in system
                if isinstance(item, dict) and item.get("type") == "text"
            )
        elif not isinstance(system, str):
            system = LogWatcher._extract_message_text(system)

        if system:
            parts.append(system)

        return "\n\n".join(part for part in parts if part)

    def _broadcast_claude_messages(self, request_id: str, messages: list):
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            sys_texts, regular_text = self._split_system_reminders(content)

            for st in sys_texts:
                broadcast_event({
                    "type": "context_message",
                    "id": request_id,
                    "role": "system-reminder",
                    "content": st[:10000],
                })

            if not regular_text:
                continue
            is_last_user = (role == "user" and i == len(messages) - 1)
            broadcast_event({
                "type": "context_message",
                "id": request_id,
                "role": role,
                "content": regular_text[:10000],
                "is_last": is_last_user,
            })

    def _broadcast_openai_input_messages(self, request_id: str, input_items: list):
        last_user_index = None
        for idx, item in enumerate(input_items):
            if isinstance(item, dict) and item.get("role") == "user":
                last_user_index = idx

        for idx, item in enumerate(input_items):
            if not isinstance(item, dict):
                continue

            role = item.get("role")
            if not role:
                continue

            content = self._extract_message_text(item.get("content", ""))
            if not content:
                continue

            mapped_role = self._map_openai_role(role)
            event = {
                "type": "context_message",
                "id": request_id,
                "role": mapped_role,
                "content": content[:10000],
            }
            if mapped_role == "user":
                event["is_last"] = (idx == last_user_index)
            broadcast_event(event)

    def _get_client_state(self, client: str):
        client = (client or "__default__").strip().lower()
        return self.client_states.setdefault(client, {
            "current_request_id": None,
            "current_aggregator": None,
            "current_session_id": None,
            "request_queue": deque(),
            "response_to_request": {},
            "item_to_request": {},
            "last_response_id": None,
        })

    @staticmethod
    def _split_client_tag(message: str):
        m = re.match(r"\[([^\]]+)\]\s*(.*)", message, re.DOTALL)
        if m:
            return m.group(1).strip().lower(), m.group(2)
        return "__default__", message

    @staticmethod
    def _latest_request(state: dict):
        queue = state.get("request_queue")
        if queue:
            return queue[-1]

        legacy_request_id = state.get("current_request_id")
        legacy_aggregator = state.get("current_aggregator")
        if legacy_request_id or legacy_aggregator:
            request_id = legacy_request_id or getattr(legacy_aggregator, "request_id", None) or str(uuid.uuid4())
            aggregator = legacy_aggregator or SSEAggregator(request_id)
            request = {
                "id": request_id,
                "aggregator": aggregator,
                "response_id": None,
                "item_ids": set(),
                "body_received": False,
                "session_id": state.get("current_session_id"),
            }
            state["request_queue"].append(request)
            return request

        return None

    @staticmethod
    def _sync_legacy_state(state: dict):
        queue = state.get("request_queue")
        latest = queue[-1] if queue else None
        if latest:
            state["current_request_id"] = latest["id"]
            state["current_aggregator"] = latest["aggregator"]
        else:
            state["current_request_id"] = None
            state["current_aggregator"] = None

    def _pop_oldest_request(self, state: dict, session_id: str = None):
        queue = state.get("request_queue")
        if not queue:
            return None
        request = None
        if session_id:
            for idx, candidate in enumerate(queue):
                if candidate.get("session_id") == session_id:
                    request = candidate
                    del queue[idx]
                    break
        if request is None:
            request = queue.popleft()
        response_id = request.get("response_id")
        if response_id:
            state["response_to_request"].pop(response_id, None)
            if state.get("last_response_id") == response_id:
                state["last_response_id"] = None
        for item_id in request.get("item_ids", set()):
            state["item_to_request"].pop(item_id, None)
        self._sync_legacy_state(state)
        return request

    @staticmethod
    def _get_sse_item_id(sse_data: dict):
        item_id = sse_data.get("item_id")
        if item_id:
            return item_id
        item = sse_data.get("item", {})
        if isinstance(item, dict):
            return item.get("id")
        return None

    @staticmethod
    def _oldest_request_without_body(state: dict):
        queue = state.get("request_queue") or []
        for request in queue:
            if not request.get("body_received"):
                return request
        return None

    @staticmethod
    def _oldest_unbound_request(state: dict):
        queue = state.get("request_queue") or []
        for request in queue:
            if request.get("response_id") is None:
                return request
        return None

    @staticmethod
    def _bind_item_to_request(state: dict, request: dict, item_id: str):
        if not request or not item_id:
            return
        state["item_to_request"][item_id] = request
        request.setdefault("item_ids", set()).add(item_id)

    def _resolve_request_for_sse(self, state: dict, sse_data: dict):
        evt_type = sse_data.get("type", "")
        response = sse_data.get("response", {})
        response_id = response.get("id")
        item_id = self._get_sse_item_id(sse_data)

        if item_id:
            request = state["item_to_request"].get(item_id)
            if request is not None:
                if response_id and request.get("response_id") is None:
                    request["response_id"] = response_id
                    state["response_to_request"][response_id] = request
                    state["last_response_id"] = response_id
                return request

        if response_id:
            request = state["response_to_request"].get(response_id)
            if request is None:
                for candidate in state["request_queue"]:
                    if candidate.get("response_id") is None:
                        candidate["response_id"] = response_id
                        request = candidate
                        break
                if request is None:
                    request = self._latest_request(state)
                    if request and request.get("response_id") is None:
                        request["response_id"] = response_id
                if request is not None:
                    state["response_to_request"][response_id] = request
            state["last_response_id"] = response_id
            if item_id and request is not None:
                self._bind_item_to_request(state, request, item_id)
            return request

        if evt_type == "response.output_item.added" and item_id:
            request = self._oldest_unbound_request(state) or self._latest_request(state)
            self._bind_item_to_request(state, request, item_id)
            return request

        if item_id:
            request = self._oldest_unbound_request(state)
            if request is not None:
                self._bind_item_to_request(state, request, item_id)
                return request

        last_response_id = state.get("last_response_id")
        if last_response_id:
            request = state["response_to_request"].get(last_response_id)
            if request is not None:
                return request

        if len(state["request_queue"]) == 1:
            return state["request_queue"][0]

        if evt_type.startswith("response.") and state["request_queue"]:
            return state["request_queue"][0]

        return self._latest_request(state)

    @staticmethod
    def _map_openai_role(role: str) -> str:
        if role == "developer":
            return "system-reminder"
        if role in ("system", "user", "assistant"):
            return role
        return "assistant"

    @staticmethod
    def _normalize_tools(tools):
        if not isinstance(tools, list):
            return []

        normalized = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            normalized_tool = dict(tool)
            if "name" not in normalized_tool or not normalized_tool.get("name"):
                normalized_tool["name"] = tool.get("type", "tool")
            if "input_schema" not in normalized_tool:
                if isinstance(tool.get("parameters"), dict):
                    normalized_tool["input_schema"] = tool["parameters"]
                elif isinstance(tool.get("input_schema"), dict):
                    normalized_tool["input_schema"] = tool["input_schema"]
                elif isinstance(tool.get("format"), dict):
                    normalized_tool["input_schema"] = tool["format"]
            normalized.append(normalized_tool)
        return normalized

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
                    if t in ("text", "input_text", "output_text"):
                        parts.append(item.get("text", ""))
                    elif t == "refusal":
                        parts.append(item.get("refusal", ""))
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
