# 测试说明

```bash
python3 test_changes.py
```

测试文件 `test_changes.py` 通过 mock `broadcast_event` 捕获事件，验证后端解析逻辑的正确性。共 **47 个用例**，分 7 组：

## 1. `_truncate_raw_log` 截断函数（4 个用例）

| 用例 | 说明 |
|------|------|
| 短行不截断 | 500 字符以内的行原样返回 |
| 长行截断到 500+后缀 | 82KB 行截断后以 `...` 开头的后缀结尾 |
| 包含总长度提示 | 后缀中包含 `82000 chars total` |
| 截断结果长度合理 | 截断后总长度 < 600 字符 |

## 2. `_extract_message_text` 消息提取（8 个用例）

| 用例 | 说明 |
|------|------|
| 纯字符串直接返回 | `"hello"` → `"hello"` |
| text 块提取 | `[{type:text, text:aaa}, {type:text, text:bbb}]` → `"aaa\nbbb"` |
| tool_use 提取 | 提取为 `[Tool Use: Read]` 格式 |
| tool_result 字符串 | content 为字符串时提取为 `[Tool Result]\n...` |
| tool_result 数组 | content 为内容块数组时提取嵌套文本 |
| 混合内容 | text + tool_use 混合数组正确拼接 |
| 空列表返回空字符串 | `[]` → `""` |
| 其他类型转字符串 | 数字等非标准类型 fallback 到 `str()` |

## 3. 请求体解析 → `context_message` 事件（13 个用例）

构造包含 system（数组格式）、2 条 user 消息（最后一条含 `<system-reminder>`）、1 条 assistant 消息的完整请求体，验证：

| 用例 | 说明 |
|------|------|
| 产生了事件 | 解析后至少产生 1 个事件 |
| 包含 context_message 事件 | 新事件类型正确 |
| 无 user_message 事件 | 旧的 `user_message` 类型不再产生 |
| 有 2 条 system 事件 | 顶层 system 和 system-reminder 各一条 |
| 顶层 system 包含 CLAUDE.md | system 数组中多个 text 项正确拼接 |
| system-reminder 被拆为独立系统块 | `<system-reminder>` 内容从用户消息中分离，作为独立系统指令块广播 |
| 有 2 条 user 消息 | 所有 user 消息都被遍历（而非仅最后一条） |
| 有 1 条 assistant 消息 | assistant 消息也被提取 |
| 第一条 user 不是 is_last | 非末尾 user 消息 `is_last=false` |
| 最后一条 user 是 is_last | 末尾 user 消息 `is_last=true` |
| 用户消息不含 system-reminder 标签 | 拆分后用户消息中不再包含 `<system-reminder>` 标签 |
| 最后一条用户消息只有纯文本 | 拆分后只剩 `"你是什么模型"` |
| 请求 ID 正确 | 所有事件的 `id` 字段一致 |

## 4. 请求体解析失败 → 截断 raw_log（5 个用例）

构造 82KB 非法 JSON 行，验证：

| 用例 | 说明 |
|------|------|
| 产生 parse_error 事件 | 解析失败正确上报 |
| 类型是 parse_error | 事件类型正确 |
| 原因包含异常类型 | reason 中包含 `JSONDecodeError` |
| raw_log 被截断 | 长度 < 600 字符 |
| raw_log 包含总长度 | 包含 `chars total` 后缀 |

## 5. SSE 解析失败 → 截断 raw_log（3 个用例）

构造 80KB 非法 SSE 行，验证：

| 用例 | 说明 |
|------|------|
| SSE 解析失败产生 parse_error | 事件正确产生 |
| SSE 错误原因正确 | reason 中包含 `SSE JSON 解析失败` |
| SSE raw_log 被截断 | 长度 < 600 字符 |

## 6. system 为字符串格式（2 个用例）

构造 `"system": "You are a helpful assistant."` (字符串而非数组) 的请求体，验证：

| 用例 | 说明 |
|------|------|
| 字符串 system 也能提取 | 产生 1 条 system 事件 |
| 字符串 system 内容正确 | 内容包含 `helpful assistant` |

## 7. `_split_system_reminders` 分离函数（12 个用例）

验证 `<system-reminder>` 标签从消息内容中拆分为独立系统指令的逻辑：

| 用例 | 说明 |
|------|------|
| 无标签：sys_parts 为空 | 纯文本字符串不产生系统部分 |
| 无标签：regular 原样返回 | 纯文本内容不变 |
| 字符串标签：提取内容 | 从字符串中提取 `<system-reminder>` 内部文本 |
| 字符串标签：剩余文本 | 移除标签后的文本保留 |
| 数组独占块：提取内容 | 整个 text 块为 `<system-reminder>` 时正确提取 |
| 数组独占块：剩余只有用户文本 | 移除后只剩其他 text 块的内容 |
| 混合块：提取内容 | 同一 text 块中标签与普通文本混合时正确拆分 |
| 混合块：剩余保留前后文本 | 标签前后的文本保留在 regular 中 |
| 多标签：提取全部 | 多个 `<system-reminder>` 块全部提取 |
| 多标签：剩余正常文本 | 非标签块保留 |
| 无标签数组：sys_parts 为空 | 无标签的内容块数组不产生系统部分 |
| 无标签数组：文本正常拼接 | 多个 text 块正常拼接为 `\n` 分隔的文本 |
