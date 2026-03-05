"""默认配置、全局状态、broadcast_event"""

import json
import os
import queue
import threading

from .utils import decode_octal_escapes

# ─── 默认配置 ────────────────────────────────────────────────────────────────

DEFAULT_LOG_FILE = os.path.expanduser("~/.cc-switch/logs/cc-switch.log")
DEFAULT_PORT = 8765
DEFAULT_INTERVAL = 5  # 秒

# ─── 全局状态 ────────────────────────────────────────────────────────────────

# 所有 SSE 客户端共享的事件队列列表（每个客户端一个队列）
client_queues = []
client_queues_lock = threading.Lock()

# 日志监控配置（可由前端动态修改）
watcher_config = {
    "log_file": DEFAULT_LOG_FILE,
    "interval": DEFAULT_INTERVAL,
    "file_changed": threading.Event(),
}
watcher_config_lock = threading.Lock()


def _decode_event_strings(obj):
    """递归解码事件数据中所有字符串值的八进制转义序列"""
    if isinstance(obj, str):
        return decode_octal_escapes(obj)
    if isinstance(obj, dict):
        return {k: _decode_event_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode_event_strings(v) for v in obj]
    return obj


def broadcast_event(event_data: dict):
    """将事件广播到所有已连接的 SSE 客户端"""
    data = json.dumps(_decode_event_strings(event_data), ensure_ascii=False)
    with client_queues_lock:
        dead = []
        for q in client_queues:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            client_queues.remove(q)
