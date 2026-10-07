# 服务器操作（CLI v1.4.0，兼容发布包协议v1）

## 定位并检查目标

确认用户授权的主机、application、environment、发布版本；沿用既有部署用户、DEPLOY_ROOT/DEPLOY_CONFIG_ROOT或命令中的目录选项。只在目标Linux主机执行运行命令。

```bash
deployctl --version
docker compose version
deployctl status "$APP" --env "$ENVIRONMENT"
```

服务运行要求Python>=3.10、Docker Engine、Compose>=2.30。CLI找不到、版本不兼容或需要更新工具时，先读取 [CLI安装与升级](cli-lifecycle.md)，处理PATH、在线/离线安装、旧版更新或self-update。安装器不安装Docker或Python，也不配置云账号。

默认状态位置 `/opt/deployments/<application>/<environment>/state.json`；配置位置 `/etc/deployctl/<application>/<environment>/{config.env,secrets.env}`。state.json为依据，status还显示实际Compose状态。

| 实际状态 | 操作 |
|---|---|
| 未安装，current为空且无transaction | install |
| 已有成功版本，transaction为空 | upgrade |
| transaction非空 | 读取状态/日志，修复原因后按恢复授权执行rollback |
| 已明确请求回滚，存在previous | rollback并验证恢复版本 |

不能因为用户说“安装最新版”就对已安装项目调用install，也不能把candidate版本当作成功版本。仅诊断请求不代表可以重启、回滚或升级；明确的部署/恢复授权可直接执行相应步骤。

## 首次安装或升级

确认包来源和预期SHA256、application、版本与目标一致；镜像仓库拉取凭证在实际部署身份下配置。缺少secret时只列名称并通过受控渠道配置，不读取或回显其值。

```bash
deployctl install "$APP" --env "$ENVIRONMENT" \
  --release "$RELEASE_URL_OR_LOCAL_PATH" --sha256 "$EXPECTED_SHA256"

deployctl upgrade "$APP" --env "$ENVIRONMENT" \
  --release "$RELEASE_URL_OR_LOCAL_PATH" --sha256 "$EXPECTED_SHA256"
```

没有显式SHA时，工具要求相邻同名 `.sha256` 文件/URL；不能省略校验以绕过下载失败。仅首次install支持 `--port`、`--bind`，后续升级保留绑定。多个项目需要不同宿主机端口。

raw配置格式为 `KEY=value`，不加shell引号、不写export、不支持多行。secrets.env权限600；secrets.env覆盖config.env重名项。升级保留配置，restart不重新应用修改的env_file。

私有GitHub URL使用GH_TOKEN/GITHUB_TOKEN，由工具解析API并移除跨主机重定向的Authorization。凭证不嵌入URL。远程执行使用安全的参数传递/引用，不能把未校验输入直接拼接进shell或输出秘密值。

执行后立即检查退出码，再运行status和实际就绪检查；服务提供版本接口时验证返回版本。成功标准是目标版本就绪且transaction为空。

## 失败和恢复

- 下载、校验、配置或拉取失败：当前服务通常尚未替换，修复具体原因；不重试破坏性操作。
- upgrade失败且旧版本恢复成功：本次发布仍失败，不能改写CI退出码。
- transaction非空：停止进一步upgrade/stop/restart，读取status/logs。根据已授权的恢复范围执行rollback，不能删除state.json或绕过工具直接compose up。
- 首次install失败：工具清理候选容器；logs可读取last-failure.log。已有服务升级失败的候选快照也在该文件，权限600，输出时注意业务敏感字段。

```bash
deployctl logs "$APP" --env "$ENVIRONMENT" --tail 100
deployctl rollback "$APP" --env "$ENVIRONMENT"
deployctl status "$APP" --env "$ENVIRONMENT"
```

回滚失败时保留pending并报告原因，不能无限重试或报告恢复完成。应用回滚不恢复数据库/配置/对象存储；发布含不可逆迁移时需先核对其恢复方案。本工具不执行迁移。

## 常用运维

status、logs用于读取；stop、restart需要相应操作授权。所有命令保持相同application/environment/root/config-root。首次失败的last-failure.json记录版本、阶段和原因，日志文件可能含业务敏感信息，不整体粘贴到对话。
