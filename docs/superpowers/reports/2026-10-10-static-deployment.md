# 静态部署实现与验收记录

在用户指定的当前 main 分支实现；基线21bdf46，主实现提交58f8984，随后追加审查修复（见Git历史）。本次交付源码、CLI 1.13.0 分发资产、管理台0.6.0、静态workflow/模板/示例/文档，以及仓库和本机team-deploy skill。正式Release、推送、云端管理服务升级不属于本次验收。

## 结果

平台登记不可变Docker/static项目类型和版本，静态包支持ZIP/tar.gz和来源commit。统一publish/install/upgrade/rollback/status/logs自动分发。--target-dir作为首次安装兜底，远端目录优先，升级/回滚沿用绑定。静态目录使用完整已验证文件树和原子链接切换，旧版本留存；跨root、同目录及父子目录冲突拒绝。失败恢复原树，恢复失败保留pending，离线回滚校验本机缓存。

静态流程无需Docker/Registry，不执行pre/post hook、不注入部署时运行变量。管理台静态表单和发布页按类型展示；组默认目录可继承/覆盖/清除，静态配置接口不返回Docker变量或组秘密。Web入口、TLS、域名与访问日志由现有Nginx/Caddy管理。

可复用工作流在构建前恢复同版本/同来源原包，来源冲突拒绝，首次构建按项目命令产出确定性归档并发布管理平台；不伪造GitHub Release URL。见[使用说明](../../static-deployment.md)。

## 已执行验证

| 检查 | 结果 |
|---|---|
| Linux Python全量 | 316个测试全部通过，无跳过 |
| Windows Python全量 | 316个测试，56个明确POSIX专用测试跳过，其余通过；POSIX能力由Linux实际验证 |
| Go test -race / vet | 全部通过，真实隔离PostgreSQL数据库和Registry |
| 管理台单元与构建 | 26个测试通过，TypeScript/Vite构建成功 |
| 浏览器 | 34个用例通过，包含真实静态上传/环境与原Docker/配置/权限流程 |
| 静态端到端 | 真实API tar.gz发布安装、ZIP升级、来源恢复/冲突、回执、平台关闭后回滚/status/logs通过；Registry不可达 |
| Docker回归 | managed控制平台与本地真实Docker集成通过，包含配置/hook/回执/离线回滚 |
| 分发与安装器 | 自包含zipapp、skill资源、摘要/版本、真实安装/别名/重装/校验失败/init通过 |
| 工作流与skill | actionlint通过；skill结构验证与RED/GREEN使用场景检查通过 |

GitHub托管Actions和实际云服务部署未在本次运行；本地工作流脚本/API/权限与产物路径已验证，模板必须引用实际可访问的提交或已发布tag。

补充安全回归已先失败后修复：丢失state不能重新接管已拥有目录；旧缓存重新解压复核包身份；共享Go/Python夹具覆盖ZIP目录属性、压缩算法、UTF-8标志、PAX大小和sparse元数据边界。

## 实施取舍

按执行顺序保留完整记录：

1. 在当前checkout开发，遵循用户指示，覆盖默认独立worktree偏好；代价是变更共享当前分支。
2. Store新增PublishReleaseAuthorized授权/存储回调，旧PublishReleaseChecked保留兼容封装。当前权限先于版本冲突检查，正式制品写入在冲突验证之后；若接口判断不合适，需调整内部回调顺序。
3. 静态变更使用只读根目录FD上的flock，并保存每目标持久所有权记录，覆盖跨root/用户的同目录及嵌套竞争；代价是主机静态操作串行，冲突时重试。
4. React表单文件命名ReleaseUploadForm.tsx，避免Windows把ReleaseUpload.tsx与releaseUpload.ts解析为大小写冲突；代价是文件路径与初始计划不同，组件API保持一致。
5. 新增scripts/static_delivery.py作为可独立验证的workflow恢复入口；代价是多维护一个内部辅助脚本。
6. 最终审查的全主机串行化排除项沿用第3项取舍与代价。
7. 工作流先验证解析后目录仍在checkout内，再传原始路径给打包器，统一根目录链接拒绝规则；代价是原先用链接产物根的项目需填写真实目录。
8. Web入口管理和URL健康检查保持在现有服务器入口，遵循已批准的边界；代价是file-deployed状态不能证明公开URL可用。

## 本机skill

19个资源与仓库逐一SHA256相符；随附CLI实际输出1.13.0。更新前备份位于 `C:/Users/yeshaopeng/Documents/Codex/2026-10-04/she/work/skill-backups/team-deploy-before-static-20261010/team-deploy`。没有复制账号凭据。

## 独立审查

独立审查无Critical/Minor，发现3项Important，作者在一次修复过程中先复现失败再修复：

- 跨root物理版本树接管：新增缓存根保护标记，在任何目标父目录/嵌套缓存创建前拒绝；回归验证A目录不变且A状态仍可用。
- checksum侧文件链接覆盖：替换输出前检查侧文件，独占临时文件/fsync/原子替换；回归验证无关文件与旧输出保持原字节。
- 常见GNU tar根记录误拒绝：Python/Go允许零负载 ./ 目录记录；真实GNU tar生成与共享夹具通过，重复根/普通文件空根/根负载仍拒绝。

另外统一工作流与本地打包器的产物根链接拒绝规则，实际执行工作流打包片段的回归先失败后通过。整套316个Linux测试、Go race/vet、静态/Docker端到端和安装器回归重新验证。没有未处理的审查minor；未安排重复审查，修复依据为上述回归与完整测试结果。
