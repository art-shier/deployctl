# 多服务部署（ctl v1.20.0）

先升级管理服务，再升级节点 Agent，确认节点上报 `multi_service_v1`。发布包 schema 3 的最低版本为 1.20.0；旧节点不能执行新包，旧单服务和静态项目继续沿用原命令。

`deploy/deployment.yaml` 使用 schema 2 的 services、jobs、volumes 和 primary_endpoint。每个服务独立指定 build 或不可变 image；外部镜像使用 mirror: project，构建流程将所有镜像放在本项目授权仓库内。配置用 config/secret/literal 显式绑定；只有声明的键进入相应服务。发布工作流仍使用 build-release.yml，调用引用与 platform-ref 必须一致。动态普通构建参数使用 service-build-args JSON 按组件指定；不要传密钥。

publish、install、upgrade、self-update 和管理端在线升级入口保持不变。`install/upgrade --plan` 只读预览；`status/logs --service NAME` 选择服务，运行日志也支持任务。默认运行日志汇总所有活动服务。--port/--bind 对应 primary_endpoint。

应用升级不会重建无关 PostgreSQL 容器或数据卷。持久服务配置变化需要 `maintain --service postgres --backup-sha256 SHA --plan`，确认可恢复的备份和计划后，使用同一参数及 `--plan-id ID` 执行。数据库镜像或卷映射变更不能用普通升级或原地维护绕过。

丢卷后使用包含恢复任务的包：`restore APP --env ENV --job-source restore=SHA --plan`，核对计划后传 `--plan-id ID`。新数据写入独立卷，旧服务先停写，旧容器与旧卷保留；恢复任务必须核对备份哈希、空目标和恢复数据，健康检查通过才切换当前部署。SHA 只是输入标识，不能代替真实恢复测试。

pending 状态先读取 operation ID 和日志；`recover APP --env ENV --operation ID` 只核对并继续原操作。失败或不确定的数据导入不自动重复。跨恢复代次不允许回滚到旧数据；不能删 state.json、手改 Compose、直接重用旧卷或运行 down -v 来绕过检查。旧钩子数据库迁移必须停写、最终备份、隔离恢复和明确入口切换，不自动接管。
