"""watcher 包 - Claude Code 日志监控核心模块"""

from .config import (
    DEFAULT_LOG_FILE,
    DEFAULT_PORT,
    DEFAULT_INTERVAL,
    watcher_config,
    watcher_config_lock,
    broadcast_event,
)
from .utils import decode_octal_escapes, parse_log_line
from .aggregator import SSEAggregator
from .log_watcher import LogWatcher
from .server import RequestHandler
