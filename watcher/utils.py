"""八进制转义解码、日志行解析"""

import re

# ─── 八进制转义解码 ──────────────────────────────────────────────────────────

OCTAL_SEQ_RE = re.compile(r"((?:\\[0-3][0-7]{2})+)")


def decode_octal_escapes(s: str) -> str:
    """将 Rust 日志中的八进制转义序列（如 \\345\\255\\246）解码回 UTF-8 中文"""
    def _replace(m):
        octals = re.findall(r"\\([0-3][0-7]{2})", m.group(1))
        try:
            return bytes(int(o, 8) for o in octals).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return m.group(0)
    return OCTAL_SEQ_RE.sub(_replace, s)


# ─── 日志行解析 ──────────────────────────────────────────────────────────────

LOG_LINE_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2})\]"   # [date]
    r"\[(\d{2}:\d{2}:\d{2})\]"   # [time]
    r"\[(\w+)\]"                  # [level]
    r"\[([^\]]+)\]"               # [module]
    r"\s+(.*)"                    # message
)


def parse_log_line(line: str):
    """解析单行日志，返回 (date, time, level, module, message) 或 None"""
    m = LOG_LINE_RE.match(line)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
