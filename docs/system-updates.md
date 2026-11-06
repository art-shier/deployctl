# 控制台系统更新

本功能面向包含此改动的 CLI 1.15.0 / Web 0.8.0 构建；源码中的版本号不代表该版本已正式发布。它更新节点上的 ctl / Agent 或中心服务，不更新业务项目版本。业务部署仍在项目的“部署”页执行。

## 一次性配置

控制台升级需要在宿主机运行独立的 `ctl-updater` 服务。它主动访问中心，不开放管理端口；升级时即使 Agent 或中心容器停止，更新程序也会继续执行并保存结果。

先按安装说明在中心和需要管理的节点上安装包含本功能的 ctl。使用系统级、官方安装器管理的命令（通常 `/usr/local/bin/ctl`）；用户可写目录、软链接或被手工修改过的命令不符合更新程序要求。Linux 需要 root、Python >=3.10 和 systemd；中心还需要现有管理服务安装器要求的 Docker / Compose。

在“系统更新”页面为指定节点或中心生成一次性更新器接入凭据。它只绑定该目标，有效期15分钟，与节点登记凭据、管理员登录凭据分开。命令在隐藏输入提示中接收凭据，不要把凭据写入命令参数。

### 节点

节点须先完成 Agent 登记、授权和 systemd 配置。首次接入更新能力时，先手动更新 ctl 并重启 Agent，确保正在运行的 Agent 也包含新版健康回报功能。仅替换磁盘上的 ctl 而不重启旧 Agent，会显示不可更新。

```bash
# 将地址、节点 ID、ctl 路径替换为本机实际值
sudo /usr/local/bin/ctl updater enroll \
  --server https://ctl.example.com \
  --kind node --target-id <控制台节点ID> \
  --cli-path /usr/local/bin/ctl \
  --agent-home /var/lib/ctl-agent \
  --agent-service ctl-agent.service
sudo /usr/local/bin/ctl updater install
sudo systemctl status ctl-updater
```

### 中心服务器

在运行中心 Docker Compose 的**宿主机**执行，不是在 API 容器内执行。中心本身应已升级到包含系统更新 API 的版本。

```bash
sudo /usr/local/bin/ctl updater enroll \
  --server https://ctl.example.com \
  --kind center --target-id center \
  --cli-path /usr/local/bin/ctl \
  --server-home /opt/ctl-platform
sudo /usr/local/bin/ctl updater install
sudo systemctl status ctl-updater
```

更新器会把可执行包复制到私有目录 `/var/lib/ctl-updater`，systemd 使用这份独立副本运行；替换 ctl 或中心容器不会终止更新器。不要删除、复制到其他服务器或编辑其中的身份和任务日志。

`--home` 可指定独立更新器目录，登记、安装服务和运行时必须一致。`ctl updater service` 只输出 systemd 单元，`ctl updater run` 用于前台排查；`--token-file` 可读取仅当前身份可读的私有凭据文件。每台主机默认一个 `ctl-updater.service`：若中心主机同时运行业务 Agent，需要分别配置独立 home，并用 `install --service-name ctl-updater-center.service` / `ctl-updater-node.service` 指定不同服务名。安装器拒绝覆盖配置不同的已有服务。

## 节点更新

控制台展示各节点的实际版本、更新器连接状态和不可用原因。选定目标版本和节点，默认逐台更新；建议先选择一个节点验证，再提交其他节点，批次最多50个目标，并发可选1～5。

版本目录只接受官方 `art-shier/deployctl` 已发布的稳定版本。任务固定版本和 SHA256，不接受任意下载地址或命令，不随 latest 改变。协议1支持1.15.0起的1.x版本；跨主版本升级需要重新评估兼容性。当前版本没有更高的兼容发布时，页面不会提供可执行升级。

提交后暂停目标节点领取新业务部署，已有远程任务继续完成。更新器下载并校验安装包，备份现有 ctl 和安装记录，正常停止 Agent、替换 ctl、重启。只有新 Agent 的进程身份、版本和新的成功心跳都符合预期，才报告成功。

更新失败时恢复旧命令和安装记录，并检查旧 Agent 是否恢复。恢复成功显示“已回退”；无法确认恢复则显示“需要处理”。业务容器不因更新 ctl 而重启。更新期间不要并发执行手动工具更新或修改 Agent 单元。

## 中心更新与备份

中心更新单独提交。更新器先暂停新的远程部署领取，并等已有远程部署完成；中心升级不与节点升级执行阶段重叠。升级期间管理台可能短暂断开，独立更新程序保留执行状态，中心恢复后补报结果。

执行前自动备份：

- PostgreSQL 的自定义格式转储。
- 中心的 keys、instance.json、compose.env 和 server-state.json。

备份保存在更新器 home 下对应任务的私有 backup 目录。内置数据库通过已安装 Compose 的 database 服务运行 pg_dump；外部数据库需要宿主机安装兼容版本的 pg_dump。数据库密码仅通过私有配置和子进程环境传递，不进入命令参数或控制台日志。备份失败时不会开始中心升级。

镜像仓库和发布包目录原地保留，自动备份不复制这些可能很大的存储。执行前管理员需确认外部备份和恢复安排。数据库迁移后不能保证旧镜像可运行，因此中心失败时保留备份、安装器 pending 状态和诊断信息，**不自动回滚数据库或切回旧镜像**。

## 失败和恢复

- 等待中的任务可取消，已领取/执行中的任务不能通过页面强杀。
- 请求结果不明确时使用页面“重试”，保持同一请求 ID，避免重复创建更新任务。同一浏览器标签页内切换页面或刷新后会恢复待确认请求；浏览器会话存储只保存非敏感的任务参数，不保存接入凭据。
- 更新器重启后读取私有日志，不盲目重放执行。中心若已完成替换，核对实际安装版本、摘要和 API 就绪后补报成功；无法确认则标记需要处理。
- 需要处理的任务会继续阻止相应节点或中心领取新部署。管理员先在宿主机修复，确认更新器已回报健康的旧版或目标版本，再在控制台点击“确认已恢复”。这会记录恢复确认，保留原始失败历史，解除调度阻塞。
- 用 `sudo journalctl -u ctl-updater -n 100` 查看固定诊断信息。检查中心可用 `sudo ctl server-status` 和 `sudo ctl server-logs`；涉及数据库恢复时先停止写入并按备份制度执行，不要直接删除 pending 标记。

每个目标只有一个有效更新器身份。重新接入会替换该目标的旧更新器身份；未完成或尚未确认恢复的更新任务必须先处理。普通项目部署凭据和节点凭据均不能创建系统升级任务，只有管理员可以操作和查看系统更新。
