"""HTTP 请求处理器 (HTTP + SSE + HTML 拼装)"""

import json
import os
import queue
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .config import (
    client_queues,
    client_queues_lock,
    watcher_config,
    watcher_config_lock,
)

# ─── HTML 页面缓存（启动时拼装一次） ──────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent.parent
_cached_html = None


def _build_html():
    """读取模板和静态资源，内联拼装为完整 HTML"""
    global _cached_html
    template = (_BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    css = (_BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")
    js = (_BASE_DIR / "static" / "script.js").read_text(encoding="utf-8")
    _cached_html = template.replace("{{STYLE}}", css).replace("{{SCRIPT}}", js)


def get_html():
    """返回缓存的完整 HTML，首次调用时自动构建"""
    if _cached_html is None:
        _build_html()
    return _cached_html


# ─── HTTP 请求处理器 ──────────────────────────────────────────────────────────

class RequestHandler(BaseHTTPRequestHandler):
    """处理 HTTP 请求: 页面、SSE 长连接、API 接口"""

    def log_message(self, format, *args):
        """静默 HTTP 日志"""
        pass

    def do_GET(self):
        if self.path == "/":
            self._serve_html()
        elif self.path == "/events":
            self._serve_sse()
        else:
            self.send_error(404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response(400, {"status": "error", "message": "Invalid JSON"})
            return

        if self.path == "/api/set-file":
            self._handle_set_file(data)
        elif self.path == "/api/set-interval":
            self._handle_set_interval(data)
        else:
            self.send_error(404)

    def _json_response(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_set_file(self, data: dict):
        path = data.get("path", "")
        if not path:
            self._json_response(400, {"status": "error", "message": "缺少 path 参数"})
            return
        expanded = os.path.expanduser(path)
        with watcher_config_lock:
            watcher_config["log_file"] = expanded
            watcher_config["file_changed"].set()
        self._json_response(200, {"status": "ok", "message": f"已切换到: {expanded}"})

    def _handle_set_interval(self, data: dict):
        try:
            interval = float(data.get("interval", 5))
            if interval < 0.5:
                interval = 0.5
            if interval > 60:
                interval = 60
        except (TypeError, ValueError):
            self._json_response(400, {"status": "error", "message": "无效的间隔值"})
            return
        with watcher_config_lock:
            watcher_config["interval"] = interval
        self._json_response(200, {"status": "ok", "message": f"轮询间隔已设为 {interval}s"})

    def _serve_html(self):
        html = get_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _serve_sse(self):
        """SSE 长连接: 保持打开，持续推送事件"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = queue.Queue(maxsize=1000)
        with client_queues_lock:
            client_queues.append(q)

        try:
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # 发送心跳保活
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with client_queues_lock:
                if q in client_queues:
                    client_queues.remove(q)
