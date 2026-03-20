"""SSE 聚合器 - 聚合 Claude / Codex API SSE 流式响应碎片，重建完整内容块"""

import json

from .config import broadcast_event


class SSEAggregator:
    """聚合 Claude Messages SSE 和 OpenAI Responses SSE。"""

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.model = ""
        self.blocks = {}  # Claude: index -> {type, text/name/input, ...}
        self.items = {}  # Codex/OpenAI: item_id -> {type, text_parts/name/arguments, ...}
        self.usage = {}
        self.stop_reason = ""

    def feed(self, sse_json: dict):
        """处理一个 SSE 事件 JSON"""
        evt_type = sse_json.get("type", "")

        if evt_type == "message_start":
            msg = sse_json.get("message", {})
            self.model = msg.get("model", "")
            self.usage = msg.get("usage", {})

        elif evt_type == "content_block_start":
            idx = sse_json.get("index", 0)
            block = sse_json.get("content_block", {})
            btype = block.get("type", "text")
            if btype == "thinking":
                self.blocks[idx] = {"type": "thinking", "text": block.get("thinking", "")}
            elif btype == "tool_use":
                self.blocks[idx] = {
                    "type": "tool_use",
                    "name": block.get("name", ""),
                    "tool_id": block.get("id", ""),
                    "input_json": "",
                }
            else:
                self.blocks[idx] = {"type": "text", "text": block.get("text", "")}

        elif evt_type == "content_block_delta":
            idx = sse_json.get("index", 0)
            delta = sse_json.get("delta", {})
            dtype = delta.get("type", "")
            block = self.blocks.get(idx)
            if block is None:
                return
            if dtype == "text_delta":
                if "text" in block:
                    block["text"] += delta.get("text", "")
            elif dtype == "thinking_delta":
                if "text" in block:
                    block["text"] += delta.get("thinking", "")
            elif dtype == "input_json_delta":
                if "input_json" in block:
                    block["input_json"] += delta.get("partial_json", "")

        elif evt_type == "content_block_stop":
            idx = sse_json.get("index", 0)
            block = self.blocks.get(idx)
            if block is None:
                return
            # 推送完整块
            evt = {"type": "content_block", "id": self.request_id}
            if block["type"] == "tool_use":
                evt["block_type"] = "tool_use"
                evt["name"] = block["name"]
                evt["tool_id"] = block["tool_id"]
                # 尝试解析 input JSON
                try:
                    evt["input"] = json.loads(block["input_json"]) if block["input_json"] else {}
                except json.JSONDecodeError:
                    evt["input"] = block["input_json"]
            else:
                evt["block_type"] = block["type"]
                evt["text"] = block.get("text", "")
            broadcast_event(evt)

        elif evt_type == "message_delta":
            delta = sse_json.get("delta", {})
            self.stop_reason = delta.get("stop_reason", "")
            usage = sse_json.get("usage", {})
            if usage:
                self.usage = usage

        elif evt_type == "message_stop":
            # 整条响应结束
            pass

        elif evt_type in ("response.created", "response.in_progress", "response.completed"):
            self._handle_response_lifecycle(sse_json)

        elif evt_type == "response.output_item.added":
            self._handle_response_output_item_added(sse_json)

        elif evt_type == "response.content_part.added":
            self._handle_response_content_part_added(sse_json)

        elif evt_type == "response.output_text.delta":
            self._handle_response_output_text_delta(sse_json)

        elif evt_type == "response.output_text.done":
            self._handle_response_output_text_done(sse_json)

        elif evt_type == "response.function_call_arguments.delta":
            self._handle_response_function_call_delta(sse_json)

        elif evt_type == "response.function_call_arguments.done":
            self._handle_response_function_call_done(sse_json)

        elif evt_type == "response.output_item.done":
            self._handle_response_output_item_done(sse_json)

        elif evt_type == "response.content_part.done":
            pass

        elif evt_type == "ping":
            pass

    def _handle_response_lifecycle(self, sse_json: dict):
        response = sse_json.get("response", {})
        self.model = response.get("model", self.model)
        usage = response.get("usage", {})
        if usage:
            self.usage = usage

    def _handle_response_output_item_added(self, sse_json: dict):
        item = sse_json.get("item", {})
        item_id = item.get("id")
        if not item_id:
            return
        item_type = item.get("type", "")

        if item_type == "message":
            state = self.items.setdefault(item_id, {
                "type": "message",
                "text_parts": {},
            })
            self._load_message_content_parts(state, item.get("content", []))
            return

        if item_type in ("function_call", "custom_tool_call"):
            self.items[item_id] = {
                "type": "tool_use",
                "name": item.get("name", "") or item.get("type", "tool"),
                "tool_id": item.get("call_id", "") or item_id,
                "arguments_json": item.get("arguments", "") or item.get("input", "") or "",
            }
            return

        self.items.setdefault(item_id, {"type": item_type})

    def _handle_response_content_part_added(self, sse_json: dict):
        item_id = sse_json.get("item_id")
        if not item_id:
            return
        state = self.items.setdefault(item_id, {
            "type": "message",
            "text_parts": {},
        })
        if state.get("type") != "message":
            return
        part = sse_json.get("part", {})
        content_index = sse_json.get("content_index", 0)
        part_type = part.get("type", "")
        if part_type in ("output_text", "input_text", "text"):
            state["text_parts"][content_index] = part.get("text", "")
        elif part_type == "refusal":
            state["text_parts"][content_index] = part.get("refusal", "")

    def _handle_response_output_text_delta(self, sse_json: dict):
        item_id = sse_json.get("item_id")
        if not item_id:
            return
        state = self.items.setdefault(item_id, {
            "type": "message",
            "text_parts": {},
        })
        if state.get("type") != "message":
            return
        content_index = sse_json.get("content_index", 0)
        text = state["text_parts"].get(content_index, "")
        state["text_parts"][content_index] = text + sse_json.get("delta", "")

    def _handle_response_output_text_done(self, sse_json: dict):
        item_id = sse_json.get("item_id")
        if not item_id:
            return
        state = self.items.get(item_id)
        if not state or state.get("type") != "message":
            return
        content_index = sse_json.get("content_index", 0)
        if "text" in sse_json:
            state["text_parts"][content_index] = sse_json.get("text", "")

    def _handle_response_function_call_delta(self, sse_json: dict):
        item_id = sse_json.get("item_id")
        if not item_id:
            return
        state = self.items.setdefault(item_id, {
            "type": "tool_use",
            "name": "",
            "tool_id": item_id,
            "arguments_json": "",
        })
        if state.get("type") != "tool_use":
            return
        state["arguments_json"] += sse_json.get("delta", "")

    def _handle_response_function_call_done(self, sse_json: dict):
        item_id = sse_json.get("item_id")
        if not item_id:
            return
        state = self.items.get(item_id)
        if not state or state.get("type") != "tool_use":
            return
        if "arguments" in sse_json:
            state["arguments_json"] = sse_json.get("arguments", "")

    def _handle_response_output_item_done(self, sse_json: dict):
        item = sse_json.get("item", {})
        item_id = item.get("id")
        if not item_id:
            return

        state = self.items.get(item_id, {})
        item_type = item.get("type", state.get("type", ""))

        if item_type == "message":
            text = self._extract_message_output_text(item)
            if not text:
                text = self._join_message_parts(state.get("text_parts", {}))
            if text:
                broadcast_event({
                    "type": "content_block",
                    "id": self.request_id,
                    "block_type": "text",
                    "text": text,
                })

        elif item_type in ("function_call", "custom_tool_call") or state.get("type") == "tool_use":
            raw_arguments = item.get("arguments", "") or item.get("input", "") or state.get("arguments_json", "")
            broadcast_event({
                "type": "content_block",
                "id": self.request_id,
                "block_type": "tool_use",
                "name": item.get("name", "") or state.get("name", "") or item.get("type", "tool"),
                "tool_id": item.get("call_id", "") or state.get("tool_id", "") or item_id,
                "input": self._parse_json_value(raw_arguments),
            })

        self.items.pop(item_id, None)

    @staticmethod
    def _parse_json_value(raw_value):
        if isinstance(raw_value, (dict, list)):
            return raw_value
        if not raw_value:
            return {}
        try:
            return json.loads(raw_value)
        except (TypeError, json.JSONDecodeError):
            return raw_value

    def _load_message_content_parts(self, state: dict, content):
        if not isinstance(content, list):
            return
        for idx, part in enumerate(content):
            if not isinstance(part, dict):
                continue
            part_type = part.get("type", "")
            if part_type in ("output_text", "input_text", "text"):
                state["text_parts"][idx] = part.get("text", "")
            elif part_type == "refusal":
                state["text_parts"][idx] = part.get("refusal", "")

    @staticmethod
    def _join_message_parts(parts: dict) -> str:
        if not parts:
            return ""
        ordered = [parts[idx] for idx in sorted(parts)]
        return "\n".join(part for part in ordered if part)

    def _extract_message_output_text(self, item: dict) -> str:
        content = item.get("content", [])
        if not isinstance(content, list):
            return ""
        parts = {}
        self._load_message_content_parts({"text_parts": parts}, content)
        return self._join_message_parts(parts)
