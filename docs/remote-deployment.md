# 远程部署与节点运维

本功能要求配套的 v1.14.0 管理服务、Web 和 CLI。已有 CLI 安装、升级、回滚命令及目录保持兼容；升级中心服务前请按管理服务指南备份。

## 工作方式

管理台保存节点、使用授权、部署实例和持久任务。Linux 主机上的 `ctl agent` 仅通过出站 HTTPS 连接管理服务，无需向公网开放 Agent 管理端口。任务的具体发布版本、镜像 digest、组与项目合并配置在提交时冻结。

同一个项目、同一个环境可以部署到多个节点，每节点独立记录版本、健康状态、配置快照和回滚历史。每节点同一项目环境只运行一份实例，Agent 在节点上串行执行任务；已有本地 CLI 与 Agent 共用部署文件锁。

当前远程 Agent 仅支持 Docker 项目。静态文件项目继续使用原有 `ctl install/upgrade/rollback` 部署，不显示远程部署入口，中心也拒绝为静态项目创建节点任务。

第一版面向内部可信项目。Docker 操作身份和宿主机 hooks 具有高权限，项目组授权不提供不可信租户之间的宿主机隔离。

## 1. 生成接入凭据

在管理台“部署节点”点击“接入节点”，生成一次性注册凭据。此时不会创建节点，也不预先分配权限。凭据由主服务安全随机生成，有效期 15 分钟、仅可成功使用一次，服务端只保存哈希，页面只在本次弹窗中展示。

该凭据不是平台 owner 或 deployer Token。不要将其放进 URL、Shell 参数、脚本仓库或业务配置。未使用的凭据过期或遗失后，重新打开接入窗口生成即可。

Agent 主动登记后，列表自动发现节点并显示“待授权”；主机名由 Agent 上报，仅供辨认，不是可信身份凭证。主服务为每个节点生成独立身份，由 Agent 私密保存，后续心跳、轮询和回报均使用这个身份。无需主服务主动连接节点。

若注册请求成功但响应丢失，或本机身份文件丢失，请在已经出现的节点上使用“重新登记”。它保留节点 ID 和授权，使旧节点身份立即失效；有活动任务时会拒绝。先取消待执行任务或恢复并回报已开始任务，避免遗失执行证据。不要为同一节点再次使用新的通用接入凭据，以免生成重复记录。

## 2. 接入 Linux 节点

节点要求 Python >=3.10、Docker Engine、Docker Compose >=2.30。安装包含本功能的 `ctl` 后，以拥有 Docker 权限的固定运行身份执行（以下以 root 为例）：

```bash
sudo ctl agent enroll --server https://ctl.example.com
```

命令隐藏输入一次性注册凭据。也可使用 `--token-file /私有路径/enrollment.token`，文件须当前身份所有、0600；读取后自行安全移除。注册成功将独立节点身份写入 `/var/lib/ctl-agent/node.json`，目录 0700、文件 0600。不得把这个目录复制给另一台服务器。

前台排查可以运行：

```bash
sudo ctl agent run
```

安装常驻服务（`--executable` 必须指向此节点已安装的实际 ctl 路径）：

```bash
sudo ctl agent service --executable /usr/local/bin/ctl \
  | sudo tee /etc/systemd/system/ctl-agent.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now ctl-agent
sudo systemctl status ctl-agent
sudo journalctl -u ctl-agent -n 100
```

使用用户安装的 ctl 时，将 `/usr/local/bin/ctl` 换成实际绝对路径。systemd 单元默认 root 身份，以兼容宿主机 hooks；若改为专用部署用户，须确保 Docker、部署目录与配置目录权限一致。

`enroll/run/service` 支持 `--home`。`run/service` 还支持 `--root`（默认 `/opt/deployments`）、`--config-root`（默认 `/etc/deployctl`）。注册与运行必须使用同一身份与 home；手动 CLI 操作应指定相同部署/配置目录。`service` 仅输出单元，不会自行修改系统。

不要在业务容器内启动 Agent。若以后容器化运行 Agent，需要另行设计宿主机 hooks、路径映射与 Docker socket 权限。

## 3. 分配授权并从项目部署

节点接入后，在“部署节点”找到待授权主机，点击“分配授权”，设置允许的项目组、单独项目、排除项目及环境。节点需启用，并至少选择一个组或项目及一个环境，才能完成首次授权。待授权节点可以上报心跳和轮询，但不会出现在项目的可部署节点列表，不能获得部署任务和物料。

组授权动态覆盖组内项目，排除项目优先；空组/项目不授权项目，空环境不授权任何环境。授权状态和连接状态分别显示：“在线”不代表“已授权”。

进入项目“部署”页签，选择环境、节点、操作和版本，再提交任务。可给同一个项目环境逐个选择不同节点。安装和升级可指定主机端口及绑定地址；升级未指定时沿用原绑定。回滚使用该节点之前成功的实际配置快照。

端口默认仍监听 `127.0.0.1`。跨服务器网关不能访问这个地址；应显式配置节点的私网地址或 `0.0.0.0`，并在防火墙中限制业务端口的来源。新增节点也要具备业务数据库、缓存、文件存储等依赖的访问条件。

任务提交成功后关闭浏览器不会取消任务。网络结果不确定时，页面的重试复用同一请求 ID，不要另外创建同样任务。`queued/leased` 可以取消，已运行任务不能通过 Web 强杀。

节点禁用停止新任务领取、开始及物料下载，保留业务容器和已有运行任务的结果上报。禁用节点不等于停止业务。

## 4. 状态与故障恢复

- 节点“在线”表示最近 90 秒内有心跳，不代表业务健康。Docker 可用性、实例健康、实际版本和最近观测时间分别显示。
- 心跳独立于 Docker 采集及结果补报。部署正在占用本地锁时保留上次观测时间；实际采集失败显示未知，不将缺失数据解释为卸载。
- 中心重启不丢任务。节点断网不停止正在进行的本地部署，结果写入私有日志后补报。
- `failed` 表示部署失败；若旧版本恢复成功，实例仍显示旧版本。若恢复也失败，实例的未完成事务标记要求先回滚。
- `needs_attention` 表示进程重启或开始请求结果不明确。Agent 不重放该任务，以免重复执行 hooks。检查本地 `ctl status <项目> --env <环境>`、`last-failure.log` 和私有任务日志；存在未完成事务时执行回滚，确认状态后再提交新任务。
- 回滚只恢复应用版本与配置，不撤销数据库迁移、文件写入或其他 hooks 外部副作用。
- 远程回滚依赖本地受检镜像缓存。缓存缺失时失败，不获取全局 Registry 凭据；应先由管理员按既有流程恢复可信镜像。

Agent 在 `/var/lib/ctl-agent/tasks/` 保存不含配置明文的任务执行日志。请保留这些文件以识别重复投递，不要通过删除日志强行重试。损坏日志会阻止守护进程继续执行，须结合中心任务和本地部署状态人工恢复。

systemd 停止时会等待当前部署结束（默认最多 900 秒）；超过停止时限被强制终止时，下次启动报告需要检查。升级 Agent 前宜等待节点任务完成，并保留身份和任务目录。

## 5. 手动负载均衡

可使用已有 Nginx/HAProxy/云负载均衡，后端填写各节点实例的实际可达地址及端口。例如两个内部节点：

```nginx
upstream notes_prod {
    server 10.0.0.11:8080 max_fails=3 fail_timeout=10s;
    server 10.0.0.12:8080 max_fails=3 fail_timeout=10s;
}
server {
    listen 80;
    server_name notes.example.com;
    location / {
        proxy_pass http://notes_prod;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

此片段仅示意 HTTP 转发和被动失败检测；HTTPS、主动就绪探测、超时、WebSocket、请求体限制等需按业务入口配置。不要将示例当成完整生产网关配置。

扩容：接入新节点 → 部署选定版本 → 确认健康及网关到实例的连通性 → 加入后端。缩容/升级：先摘除流量 → 等待在途请求完成 → 停止或升级实例 → 验证后再加入。第一版不会自动修改网关或实施零停机滚动发布。

业务应用需支持共享会话/文件存储、后台任务去重以及新旧版本数据库兼容。增加应用节点不会自动解决数据库瓶颈或单台网关故障。

## 验证与开发

```bash
python -m unittest discover -s tests -p test_node_agent.py -v
cd control-server
# 仅指向可创建/销毁测试数据库的隔离 PostgreSQL 实例
CTL_TEST_DATABASE_URL='postgres://.../postgres' CTL_TEST_PYTHON=python go test ./...
```

`TestRemotePythonAgentProtocol` 使用真实 Go HTTP 服务、PostgreSQL、加密规格、发布包及 Python Manager，只有 Docker 操作被模拟。Linux CI 的 `tests/control_plane_integration.py` 另覆盖真实 Docker 路径，不能用模拟测试代替实际部署验证。
