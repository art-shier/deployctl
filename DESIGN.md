# deployctl 管理端设计

## Overview
中文运维管理端，围绕项目、环境、节点和部署任务组织信息。沿用现有紧凑工具界面，不为单个功能重做品牌或导航。

## Colors
运行时颜色由 control-web/src/workspace.css 覆盖 styles.css 的基础主题。当前正文 #1e293b，页面 #f5f7fb，主色 --green #2563eb（沿用旧变量名），边框 --line #e2e8f0，表面 --surface #fff。日志使用现有文字和表面颜色，错误沿用 notice / error。

## Typography
沿用 workspace.css 的系统 UI 和 Microsoft YaHei 字体栈。日志正文使用系统等宽字体，保留换行，长行在日志区域内滚动，不撑开页面。

## Layout
项目部署页保留自然页面滚动。日志入口位于实例与任务的操作列，查看器复用 main.tsx 的 Modal（焦点恢复、Escape、对话框语义）。查看器宽度限制在视口内，筛选在窄屏折行，正文高度独立限制。

## Components
请求复用 api.ts，选择使用原生 select，时间输入为原生 datetime-local（浏览器本地时间，发送 UTC），查询为显式提交。刷新仅按用户操作发生。错误 role=alert，加载 role=status。请求参数和日志正文不写 URL 或本地存储。下载明确命名“下载当前结果”，不得暗示完整历史导出。

## Do's and Don'ts
切换实例/来源或关闭即取消请求；旧响应不得覆盖新选择。空日志、离线、超时、历史不存在和截断分别解释。不解析日志为 HTML。不改变部署表单和其它页签布局。
