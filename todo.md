# TODO — 后续改进计划

## Bug

## 优化

## 延后进行
- [ ] 板块颜色优化
- [ ] Windows 适配
- [ ] Session 对话用量统计
- [ ] 视觉颜色优化
- [ ] 主动中断支持
```
[2026-03-04][18:27:39][TRACE][axum::serve] failed to serve connection: connection closed before message completed
[2026-03-04][18:27:39][TRACE][axum::serve] connection 127.0.0.1:55259 closed
```

## 已完成
- [x] 点击设置后的弹框（toast）会被错误面板遮挡,改为在页面中间的上方出现,并且后自动消失
- [x] 内容过长时不直接展开，而是在一个框内,要点击下方的'展开'交互,再展开
- [x] 内容块增加一键复制按钮,位置在每个框的右侧,如'系统指令'的右侧
- [x] 对话支持 JSON 格式复制（原始报文）按钮位置在模型名称和状态的右侧
- [x] 日志-因为是使用文件末尾读,所以原始日志文件大不影响

