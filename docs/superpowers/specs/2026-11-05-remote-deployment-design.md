# 远程部署与多节点实例设计

## 已确认的目标

内部可信团队使用。远端 Linux Docker 主机运行轻量 Agent，主动访问现有管理服务；Web 管理节点及其项目组、项目、环境授权，在项目中选择节点部署、升级或回滚。同一个项目环境可部署到多台服务器，未来由负载均衡共同接收流量。

本阶段不做不可信租户隔离、同机同项目同环境多实例、自动扩缩容、自动配置网关、在线终端或任意 Shell 任务。负载均衡先手动配置。节点授权不等于宿主机隔离，现有宿主机 hooks 保留可信产物语义。

## 方案取舍

- SSH 推送可沿用现有工作流，但需要中心直达节点及持有 SSH 凭据，不作为新增控制通道。
- 采用出站 HTTPS 长轮询 + 独立心跳 + PostgreSQL 持久任务；不新增消息中间件。
- WebSocket/反向隧道留给后续实时终端等需求；本阶段不依赖。
- 执行端复用 Python ctl 的 Manager、包校验、配置快照、文件锁和失败恢复，不重写 Docker 部署引擎。
- Agent 以宿主机 systemd 服务运行。容器化 Agent 涉及宿主机路径与 hooks 语义，本阶段不提供。

## 对象与约束

- Node：独立 ID、名称、enabled、pending_authorization、组/项目/排除项目/环境授权、注册时间、最后心跳、Agent/平台/Docker 状态。
- 环境授权必须显式列出；空 environments 不授予任何环境。组和项目均为空时不授予任何项目，排除项目优先。
- Instance：独立 ID、node_id、project、environment，唯一约束 (node_id, project, environment)；actual_version、health、observed_at、binding、pending_transaction。业务状态与连接状态分别展示。
- Task：独立 ID、instance_id、node_id、project、environment、action、actor、request_id、status、stage、时间及非秘密结果。action 仅 install/upgrade/rollback。任务持有加密部署规格，普通查询永远不返回配置明文。
- 每节点同时最多一个非终态任务被领取；未领取任务可排队。每实例最多一个未完成任务。失败且有本地未完成事务时，只能先回滚恢复。
- 多节点不共享版本状态：升级失败的节点仍显示旧版本。未来发布批次可聚合多份节点任务。

## API v1 契约

所有路径以 /api/v1 开头，管理接口沿用现有 owner/session 或 scoped token。Web 当前仅 owner 登录，不扩展登录体系。

### 管理接口

- GET /nodes → Node[]。owner 查看全部；deployer 仅能通过项目可用节点接口查看授权范围。
- POST /node-enrollments，body {} → 201 {enrollment_token:string,expires_at:string}。owner；一次性凭据有效 15 分钟，只展示一次，只存哈希，不创建节点、不带授权。旧 POST /nodes 预建节点接口保留兼容，不再用于 Web 接入。
- PATCH /nodes/{id}，body 同上加 enabled:boolean → Node。owner，完整替换可编辑字段。
- POST /nodes/{id}/enrollment → {enrollment_token,expires_at}。owner，重新登记轮换节点身份，旧身份立即失效；有活动任务时拒绝。
- GET /projects/{slug}/nodes?environment=prod → Node[]。仅返回操作者有 resolve 权限且节点允许该项目/环境的节点；禁用节点不列出，离线节点标记离线。
- GET /projects/{slug}/instances → Instance[]。project.read，按调用者环境权限过滤。
- GET /projects/{slug}/tasks → Task[]（最近 200 条，无秘密）。resolve 权限按环境过滤。
- POST /projects/{slug}/tasks，body {node_id,environment,action,version?:string,request_id:string,host_port?:number,bind_address?:string} → 201 Task。request_id 为 32 位十六进制，由浏览器一次提交生成；同操作者同 request_id 的同内容请求返回原任务，内容不同 409。version 省略时冻结环境目标；rollback 不接受版本/端口覆盖。
- POST /projects/{slug}/tasks/{id}/cancel → Task。仅 queued/leased 可取消；running 不强杀。执行前 start 与 cancel 在数据库串行互斥。

Node 公共字段：id,name,enabled,groups,projects,excluded_projects,environments,created_at,last_seen（可空）,agent_version,platform,docker_available,online。online 表示最近 90 秒心跳且已登记，不代表业务健康。
Instance 公共字段：id,node_id,project,environment,actual_version,health,observed_at（可空）,binding（可空：{host_port,bind_address}）,pending_transaction。
Task 公共字段：id,instance_id,node_id,project,environment,action,actor,request_id,status,stage,version,created_at,updated_at,result（无秘密固定错误码/阶段及 observation）。

### Agent 接口（独立节点 Bearer 身份，不接受用户会话）

- POST /node/enroll，Bearer 一次性凭据，body {agent_version,platform,name?} → {node_id,token}。事务内消费凭据；通用凭据首次兑换时创建 enabled=true、pending_authorization=true、空授权的节点，name 使用上报主机名（仅作展示），缺省生成名称；只存 token 哈希。节点专属的重新登记凭据保留原 ID、名称和授权。注册失败且响应丢失时需管理员重新生成凭据，不重复公开身份。
- POST /node/heartbeat，body {agent_version,platform,docker_available,instances:[Observation]} → {enabled:boolean,instances:[{id,project,environment}]}。只更新已分配给当前节点的实例，拒绝外节点 ID；响应提供要观测的实例清单。
- POST /node/poll，body {wait_seconds:0..25} → {task:AgentTask|null}。每节点返回已有 leased/running 任务，或原子领取最早可执行 queued 任务；无任务可等待，不持有数据库连接等待。领取前重新检查操作者有效性、当前组成员关系、节点授权、项目存在性与任务有效期。
- POST /node/tasks/{id}/start，body {} → AgentTask。leased → running；再次请求幂等返回 running；重新检查授权、有效期，已取消或终结返回 409。
- POST /node/tasks/{id}/report，body {status,stage,observation?:Observation} → Task。status 仅 running/succeeded/failed/needs_attention；终态重复同结果幂等，不能覆盖终态。stage 仅接受固定阶段代码，不接收任意日志。disabled 节点仍可回报已开始任务，但不可领取或下载新物料。
- GET /node/tasks/{id}/package → 受限发布包字节；仅当前节点活动 install/upgrade 任务，按冻结 release 提供，不允许任意 artifact ID。
- GET /node/tasks/{id}/registry → {registry_host,token}；仅活动任务指定镜像仓库 pull，5 分钟 token。外部 Registry 返回空 token，沿用宿主机 Docker 凭据。rollback 本阶段使用本地受检缓存，缺少缓存时明确失败，不授予全局拉取权限。

AgentTask = Task 公共字段 + resolution（install/upgrade 时，沿用 PlatformClient.validate_resolution 的原始格式；package_path 改为任务 package 路径）、host_port、bind_address。冻结 resolution 的配置必须包括组与项目合并的完整配置，密文保存，不重新 resolve。
Observation = {id,project,environment,actual_version,health,binding,pending_transaction}；health 为 unknown/healthy/unhealthy/stopped/not_installed；实际观测失败上报 unknown，不伪报 healthy。服务端使用自己的接收时间记录 observed_at。
固定 stage：accepted,preparing,downloading,pulling,deploying,verifying,rollback,completed,execution_failed,interrupted。stage 不携带配置、hook 输出、环境值或异常原文。

## 任务可靠性和离线语义

queued → leased → running → succeeded / failed / needs_attention；queued/leased 可 cancelled/expired。任务开始期限为创建后 24 小时。leased 不因超时自动转交或重复执行；running 离线显示失联，不自动判失败。

Agent 私有目录默认 /var/lib/ctl-agent，凭据与任务日志 0600、目录 0700，拒绝符号链接和非当前身份文件。守护进程持有本地独占锁，工作线程执行部署，主循环独立上报心跳。状态默认沿用 /opt/deployments 和 /etc/deployctl，与人工 CLI 共享现有服务锁。

执行顺序：收到任务 → 校验协议 → 本地持久化 starting → 请求 start → 本地持久化 running → 执行 → 本地持久化终态/待上报 → 上报结果。确认终态前不清理日志。进程重启发现 starting/running：不重新执行，报告 needs_attention；如果已有完成记录则只补报。这一策略允许少量未执行任务需人工重试，避免不可逆 hooks 重复执行。断线不中断正在进行的本地部署；中心重启不丢失任务。

needs_attention 为终态，但本地 transaction 仍必须通过回滚处理。未执行的任务可重新创建；有未完成本地事务的实例拒绝新安装/升级。自动恢复不得重跑 hooks。

恢复补充：starting 日志可能写在 start 请求前，因此 leased 也允许报告 needs_attention（包括禁用节点）。忙碌实例不覆盖上次观测，由 Web 明确显示陈旧状态；真正观测失败上报 unknown，保留此前已知版本/绑定及未完成事务标记。心跳发送与 Docker 观测、终态补报分别调度，Docker 卡顿或单条结果上传超时不得阻塞节点心跳。

禁用节点：拒绝领取/start/物料访问，允许回报已运行结果；不停止业务容器。重新登记：拒绝活动任务，轮换身份；节点本地任务记录不随重登删除。组/项目权限变更在任务领取与 start 重新校验，已运行任务按已授权的本地事务完成。

## Agent 与 CLI

新增 ctl agent enroll --server URL [--token-file PATH] [--home PATH]；没有 token-file 时隐藏输入。新增 ctl agent run [--home PATH] [--root PATH] [--config-root PATH]，以及 ctl agent service [相同路径参数] 输出 systemd 单元，安装步骤写入运维文档。Linux 运行，纯协议/持久化测试可跨平台。

复用 Manager.deploy 的 managed_runtime/managed_params/management_source/deployment_defaults 参数执行冻结规格，不通过 Shell 拼装 ctl 命令。部署步骤用安全进度阶段上报，原始日志仍保留在本地；前端显示阶段、状态、版本和结果。Agent 单独的凭据不进入应用环境或配置快照。注册、轮询、回报不得将凭据放入 URL 或命令行。

## Web 工作流

沿用现有视觉组件。侧栏新增“部署节点”，支持接入说明、生成并单次显示注册凭据、自动发现待授权节点、分配授权、禁用和重新登记，清楚标注失联与禁用区别。组/项目/环境授权选项可从现有接口加载。

项目新增“部署”页签：环境选择、已部署实例表、版本/节点选择及端口/绑定设置、安装/升级/回滚、任务列表与待执行取消。每次操作只选一节点，允许重复给同一项目环境添加不同节点。任务创建后轮询刷新，页面离开停止请求。操作中禁用重复提交，失败保留表单；网络结果不确定时同一 request_id 重试。

## 负载均衡与扩展

第一版：手动将 healthy 实例配置到 Nginx/HAProxy/云 LB；默认绑定仍为 127.0.0.1，跨机网关需显式使用受防火墙保护的可达地址。扩容先部署并验证再加入流量；缩容先摘流并等待请求完成再停服务。

后续：发布批次、网关后端注册/摘除、容量约束下的滚动发布。共享会话/文件存储、后台任务去重、数据库迁移兼容由业务项目承担；增加应用节点不能自动消除数据库瓶颈或网关单点。

## 验收

- 一次性注册失效、身份隔离、跨节点任务/物料拒绝、节点禁用与组变更生效。
- 同一项目环境可在不同节点建立独立实例；同节点不重复实例；同实例并发操作冲突。
- 排队期间版本/组配置/项目配置变更不改变任务规格；普通列表和日志不泄密。
- 重复创建、领取、start、report 和 Agent 重启均不重复执行；中心重启后任务仍存在。
- 失联不等于业务故障，任务失败与恢复结果分离；本地 pending transaction 阻止错误升级。
- 保持原 CLI 行为，Python/Go/Web 自动化检查通过；无 Docker 环境明确区分模拟测试与真实 Linux Docker 验证。

## 2026-11-06 接入流程修订

节点列表每 5 秒刷新，包括接入弹窗打开期间。生成凭据不新增节点；Agent 登记后出现待授权节点。PATCH /nodes/{id} 在节点 enabled、组或项目非空、环境非空时清除首次待授权状态。Node.Allows 同时检查待授权标志及当前授权范围；待授权仍可心跳与空轮询。凭据只留在弹窗瞬时状态，关闭或导航后清除。
