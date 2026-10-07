# 团队标准部署工具包设计

目标：GitHub 项目通过统一描述文件构建不可变镜像和发布包，在 Linux 云服务器上以同一套命令安装、升级和回滚。

第一版：一个发布包对应一个无状态 HTTP 服务。Python >=3.10，Docker Engine 和 Compose >=2.30。依赖 PyYAML 6.0.3；发行版 zipapp 内置纯 Python YAML 库。平台仓库提供 reusable workflow、CLI、模板和示例。业务项目填写 deployment.yaml；流水线生成 release.yaml、compose.yaml、.env.example、README.md 和外部 SHA256。生产配置独立保存，使用 Compose raw env_file，避免密码中的 $ 被展开。

运行约定：部署目录 /opt/deployments/<application>/<environment>；配置目录 /etc/deployctl/<application>/<environment>。首次 install，后续 upgrade；下载支持本地文件、HTTPS、私有 GitHub Release（GH_TOKEN）。下载凭证仅发往对应 GitHub API，不传给重定向后的文件存储。每个服务/环境加进程文件锁；准备和拉取成功后才变更正在运行的服务。确认容器实际镜像、标签和健康检查后提交 state.json。

失败语义：升级失败时用旧发布配置恢复，验证恢复结果，原升级仍返回非零。恢复失败或进程被中断时，记录未完成事务，阻止下一次升级；使用 rollback 恢复。首次安装失败停止候选容器。操作日志记录版本和结果，不记录配置值。不执行发布包任意脚本、不自动迁移数据库、不清理持久化卷。配置变化、外部数据、数据库不在应用回滚范围内。

安全边界：只消费可信团队产物；外部 SHA256提供完整性校验，不代表独立来源认证。HTTPS URL自动获取同名 .sha256；本地包默认读取同名 .sha256，也可显式提供期望值。归档只允许固定文件、普通文件、有限大小，无符号链接、路径穿越或重名。服务器重建并比对 Compose 模板，拒绝非标准运行定义。SSH 部署模板核对 known_hosts，不关闭主机验证；具备 Docker 权限的部署身份应视为高权限。

边界：第一版为单机替换，存在短暂中断；不提供多机编排、零停机、自动数据库迁移、共享文件、网关或证书配置。提供 GitHub Linux Docker 集成测试。当前开发主机无 Docker，只能本地验证逻辑和示例接口；真实 Docker/SSH/Release 测试由接入后的 CI 执行。

验收：合法配置生成标准包；非法字段和未固定镜像被拒；配置缺失时不启动；安装成功提交版本；升级失败恢复旧版本；手动回滚可验证；恢复失败不伪报成功；归档攻击被拒；HTTP健康检查真正访问测试服务；私有下载不泄露凭证。

CLI v1.1.0新增本地init：为已有Dockerfile的项目生成部署描述和发布工作流，支持dry-run、可选部署工作流及自定义输出路径。所有目标先检查冲突，文件独占创建，失败清理本次创建的YAML；不覆盖原文件，不执行远程动作。平台仓库/引用由调用方提供，不从CLI版本推断。发布包协议保持schema_version: 1，原服务端兼容。
