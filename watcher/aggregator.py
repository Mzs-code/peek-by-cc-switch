"""SSE 聚合器 - 聚合 Claude API SSE 流式响应碎片，重建完整内容块"""

import json

from .config import broadcast_event


class SSEAggregator:
    """
    聚合 Claude API SSE 流式响应碎片，重建完整内容块。

    状态机:
      message_start → 记录 model、初始 usage
      content_block_start → 开启新块 (thinking / text / tool_use)
      content_block_delta → 累积到当前块
      content_block_stop → 推送完整的 content_block 事件
      message_delta → 记录 stop_reason、最终 usage
      message_stop → 推送 response_complete 事件
    """

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.model = ""
        self.blocks = {}  # index -> {type, text/name/input, ...}
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

        elif evt_type == "ping":
            pass
