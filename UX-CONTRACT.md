# ctl 多服务部署与日志交互

适用范围：本次多服务状态和日志选择。现有业务权限由 Go API 执行；前端不新增恢复、数据库重建或隐式回滚动作。

| 能力 | 规范所有者 | 允许变体 | 验证 |
| --- | --- | --- | --- |
| 服务状态 | ProjectDeployments / StackDetails | 只读展开详情；显示上次观测时间 | node-logs-ui.spec.ts |
| Select/Listbox | LogViewer 原生 select | 系统负责弹出层；中文可访问标签 | 键盘及服务切换浏览器测试 |
| Date | LogViewer datetime-local | 浏览器本地输入，发送 UTC | 既有日志测试 |
| Form | LogViewer | 查询显式提交；服务和来源即时切换并取消旧请求 | 失败、迟到响应测试 |
| Dialog | main.tsx Modal | 日志查看器复用焦点恢复和 Escape | 既有日志测试 |
| Scrollbar | workspace.css / styles.css | 表格和日志区域内滚动 | 窄屏测试 |
| 反馈 | LogViewer 状态文本和 notice | 加载 role=status，错误 role=alert | 既有日志测试 |

日志正文、搜索条件、恢复操作 ID 不写入 URL 或浏览器持久存储。服务选择只对当前运行日志生效，历史归档和部署日志保留原来的查询语义。未知或过期观测不能展示成当前健康；数据恢复提示不推荐自动回滚至旧库。

应用日志按项目/环境隔离；切换上下文卸载工作区并取消请求。事件查询显式提交、游标翻页；规则变更使用 version 防覆盖，冲突后重新加载。规则手动启停，无期限；缺少规则默认拒收。详情、编辑和删除复用 Modal。
