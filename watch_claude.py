#!/usr/bin/env python3
"""
Claude Code 日志监控工具 - 实时监控 CC Switch 代理日志，
聚合 SSE 碎片，以可交互的 Web UI 展示完整对话。

用法: python3 watch_claude.py [--port 8765] [--log-file PATH] [--interval 5]

零外部依赖，仅使用 Python 标准库。
"""

import argparse
import os
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from watcher import (
    DEFAULT_LOG_FILE,
    DEFAULT_PORT,
    DEFAULT_INTERVAL,
    watcher_config,
    watcher_config_lock,
    LogWatcher,
    RequestHandler,
)


def main():
    parser = argparse.ArgumentParser(description="Claude Code 日志监控工具")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP 端口 (默认 {DEFAULT_PORT})")
    parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help="日志文件路径")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help=f"轮询间隔秒数 (默认 {DEFAULT_INTERVAL})")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    # 应用配置
    with watcher_config_lock:
        watcher_config["log_file"] = os.path.expanduser(args.log_file)
        watcher_config["interval"] = args.interval

    # 启动日志监控线程
    watcher = LogWatcher()
    watcher.start()

    # 启动 HTTP 服务器
    server = ThreadingHTTPServer(("127.0.0.1", args.port), RequestHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Claude Code 日志监控已启动: {url}")
    print(f"监控文件: {watcher_config['log_file']}")
    print(f"轮询间隔: {args.interval}s")
    print("按 Ctrl+C 停止")

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        server.shutdown()


if __name__ == "__main__":
    main()
