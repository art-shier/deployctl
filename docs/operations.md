# 运维和恢复

## 目录与运行状态

```text
/opt/deployments/<application>/<environment>/
├── releases/<version>/          # 不含实际配置值
├── state.json                  # 原子更新，唯一状态依据
├── current                     # 当前成功版本的文本指针
├── events.jsonl                # 操作结果，非业务日志
├── last-failure.json           # 最近失败的版本、阶段和原因
├── last-failure.log            # 清理失败容器前的日志快照，权限600
└── .lock                       # 操作结束后可仍存在；锁由操作系统管理

/etc/deployctl/<application>/<environment>/
├── config.env
└── secrets.env                 # Linux 要求权限600
```

可在各运行命令中提供 `--root`、`--config-root`，或设置 DEPLOY_ROOT、DEPLOY_CONFIG_ROOT。不同目录被视为不同安装位置，后续操作保持一致。Docker Compose project 名包含 application/environment 的哈希，避免组合命名冲突。

## 升级失败

准备或拉取失败：不替换运行中的服务。启动/健康验证失败：尝试重新启动旧版本并验证就绪。即使旧版本恢复成功，本次 upgrade 返回非零，CI 显示失败。

```bash
deployctl status project-a --env production
deployctl logs project-a --env production --tail 200
```

status 包含最后成功版本和未完成 transaction；同时显示当前 Compose 状态。pending 状态下 logs 查看候选版本配置关联的服务日志。

首次安装失败会在清理候选容器前保存最多64 KiB日志快照；此时 logs 返回该快照。应用日志可能包含敏感业务信息，按秘密文件保管。已安装项目升级失败后，logs 显示恢复后服务的实时日志；失败版本日志保存在 last-failure.log。

恢复失败、进程终止或断电：transaction 保留，下一次 install/upgrade/restart/stop 被阻止。修复网络/配置/运行环境后执行：

```bash
deployctl rollback project-a --env production
```

pending rollback 恢复事务开始前的成功版本；正常 rollback 恢复 previous。首次 install 中断且无旧版本时，rollback 清理候选服务，不虚构历史版本。rollback 失败仍保留 pending。

不要直接删除 state.json 绕过 pending，也不要凭借 current 文本指针判断部署成功。

## 配置和数据库

升级不覆盖 config.env、secrets.env。修改配置由管理员独立管理，应用版本回滚不恢复旧配置；变更必须兼容新旧应用。

restart 重启现有容器，不重读修改后的 env_file。要应用新配置，发布一个新版本并 upgrade；第一版未提供独立 reconfigure 命令。

平台不自动执行数据库迁移。迁移应由独立、受控任务完成，优先增加字段/表等兼容变更。删除字段、不可逆数据改写等需要自己的恢复方案；不能依赖 Docker 回滚恢复数据。

## 状态/日志/停止/重启

```bash
deployctl status project-a --env production
deployctl logs project-a --env production --tail 100
deployctl stop project-a --env production
deployctl restart project-a --env production
```

restart 后验证就绪。stop 保留容器和版本，不删除卷。Docker restart policy 为 unless-stopped，主机重启后已运行服务通常自动恢复，人工停止的服务保持停止。

Docker 详细异常输出可能含敏感信息，CLI 不直接回显失败命令的任意 stderr；结合 Docker daemon 日志、应用日志和网络状态排查。应用自身日志仍可能泄露敏感字段，应由业务代码控制。

## 完整性与信任

发布包与外部 .sha256 必须匹配。URL模式的摘要来自同一发布来源，能检查损坏但不能独立证明发布者身份。生产推荐由审批过的构建输出传入 `--sha256`，并固定平台代码引用。

归档只接受固定四个普通文件、限制大小、拒绝路径穿越、链接、重名；Compose 内容必须等于模板生成结果。平台不执行包内安装脚本、不允许任意 volumes/commands/privileged 字段。

保护部署和配置目录的写权限。拥有 Docker或目录写权限的人仍可控制该服务；本工具不是不可信租户的安全沙箱。

## 可用性边界

单机 Compose 替换会有短暂中断，启动失败自动恢复也需要时间。不要将本工具的健康检查等同于全业务验收。未来可在上层增加负载均衡摘流量和多机滚动编排，继续复用相同发布包。

用户上传、数据库和其他状态不要留在容器临时文件系统。本版没有业务持久化卷接口，优先使用对象存储和托管数据库。
