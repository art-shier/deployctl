# SSH Node Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 从节点管理页通过密码 SSH 完成依赖安装、节点注册和上线检查。
**Architecture:** 节点 Bash bootstrap 处理主机变更；Go SSH 客户端仅负责安全传输和任务编排；Postgres 保存非秘密任务状态；React 表单确认密钥并轮询任务。
**Tech Stack:** Bash, Python standard library, Go x/crypto/ssh, PostgreSQL, React/TypeScript.
**Spec:** docs/superpowers/specs/2026-11-12-ssh-node-install.md

## Global Constraints
- Python >=3.10、Docker Engine >=20.10、Compose >=2.30。
- 仅 owner；密码不持久化；主机密钥必须匹配。
- 自动安装仅 Ubuntu 22.04/24.04/26.04/26.10、Debian 12/13。
- 不升级、卸载或重启已有 Docker；不覆盖已有 Agent。

## Review Focus
- 已有 Docker 不可用时不得尝试重装。
- 主机密钥变化时不得发送密码。
- 页面重试及中心重启不得重复执行安装。
- 远程日志包含秘密时不得透传到管理端。
- 注册成功但没有心跳不得显示安装成功。

### Task 1: 节点安装器
Files: internal/nodeinstall/bootstrap.sh, bootstrap_test.go, scripts/build_release_assets.py, scripts/build_agent_assets.py, bundled skill assets.
Interface: bash bootstrap.sh SERVER CLI TOKEN; stdout 固定 CTL_STAGE/CTL_ERROR 码。
- [x] 添加模拟主机命令测试并确认失败。
- [x] 实现安全预检查、官方 apt 安装、CLI/Agent 安装与本地日志。
- [x] 运行 bootstrap 测试，验证重复安装、已有 Docker、旧版本、未知发行版。

### Task 2: SSH 和后台任务
Files: internal/nodeinstall/ssh.go, internal/store/node_install.go, internal/httpapi/node_install.go and schema/routes.
Interface: Probe(ctx, Target) -> fingerprint; Run(ctx, Request, bundle, stage callback) -> error; GET/POST /node-installations and POST /node-installations/probe.
- [x] 添加 SSH 本地测试服务器测试和存储集成测试，确认失败。
- [x] 实现密钥校验、无回显 sudo、受限并发和持久任务记录。
- [x] 将一次性注册凭据关联任务，仅匹配此注册的节点新心跳可完成。
- [x] 测试权限、取消、超时、幂等、重启和秘密不泄漏。

### Task 3: 管理界面与整体验证
Files: control-web/src/NodeInstall.tsx, NodesPage.tsx, browser tests, DESIGN.md, docs.
Interface: SSH form + recent job list using above endpoints, existing modal.
- [x] 添加浏览器测试覆盖指纹、提交清空密码、失败/成功与刷新恢复。
- [x] 实现表单和状态列表，补齐使用说明。
- [x] 运行 Go、Python、前端 build/测试，独立审查并修复。

## Execution record

All three implementation tasks completed on branch `codex/ssh-node-install`.

- Go full suite passed with real PostgreSQL; SSH/store/httpapi race tests passed.
- Local real SSH server tests verify key pinning before password authentication, root/sudo input framing, private tar members, and filtering arbitrary remote output.
- Frontend unit tests: 43 passed; production build passed; node installation and existing node workflows: 12 browser cases passed, including narrow-screen rendering.
- Targeted Python Agent/bootstrap/public asset tests passed. Public bootstrap matches the canonical embedded script.
- Independent reviewer found `/var/tmp` incompatible with Agent trusted paths; moved transport staging to root-private `/run`, added trusted-path and transport regression coverage; re-review found no blockers.
- Ruling: existing Agent or CLI stops automatic bootstrap and directs to manual onboarding. This avoids overwriting existing identities or downgrading managed CLI installations.
- External validation still outstanding: a clean supported Linux host with real apt installation and systemd startup. Local machine is macOS with no Docker daemon or target SSH test host. No real host was changed and no release was published.
