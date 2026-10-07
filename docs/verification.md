# 验证记录（2026-10-04）

## 已执行

| 检查 | 结果 |
|---|---|
| `python -m unittest discover -s tests -v` | 30个测试通过 |
| 自包含 zipapp 构建 | 通过；使用 `python -I -S` 在禁用环境依赖时运行 |
| zipapp错误退出码 | 缺失配置返回1，不会误报成功 |
| Python wheel构建 | 通过 |
| GitHub工作流 actionlint v1.7.12 | 4个公共工作流及3个项目模板/示例通过；未调用shellcheck/pyflakes |
| 工作流脚本与安装脚本 Bash语法 | 通过；嵌入Python也通过语法检查 |
| JSON Schema | 两份Schema及项目/生成发布描述通过jsonschema校验 |
| 示例HTTP服务 | 实际进程的/version、就绪200和503已验证 |
| 独立代码复核 | 发现的问题已修复，并加入回归测试 |

测试覆盖：标准包生成、字段和digest约束、归档路径/链接/重名、压缩元数据膨胀、完整性校验、私有GitHub API请求与重定向凭证边界、真实本地HTTP探测、安装和升级状态、升级失败恢复、恢复失败阻断、人工回滚、同版本重试、进程中断恢复、配置保留、首次失败日志、诊断磁盘写失败，以及分发程序的退出码。

Docker边界使用测试替身；网络资产下载使用受控响应，未访问真实私有项目。测试结果证明本地逻辑，不能代替生产集成验证。

## 尚未执行

- 真实Docker安装、容器镜像拉取和Compose生命周期：当前Windows主机没有Docker。
- 真实GitHub Actions构建并上传Release：尚未提供或连接实际平台/业务仓库。
- SSH云服务器部署：尚未提供目标服务器和凭证。
- 生产流量、数据库迁移、可用性和备份恢复验收。

仓库的ci.yml和platform-release.yml已配置Linux真实Docker集成测试。发布平台仓库并接入项目后，应先让这些测试通过，再在测试服务器演练首次安装、升级失败和回滚。

## Agent skill补充验证（2026-10-07）

- skill-creator格式校验通过；UI metadata为UTF-8，默认提示包含$team-deploy，相对资源引用有效。
- 自包含skill内CLI在`-I -S`模式下执行项目模板校验，校验和与错误退出码测试通过；全仓测试增加至33个，均通过。
- 无skill的隔离对照：agent从源码识别端口和健康接口，但因缺少专用字段/工作流契约，正确报告信息缺口并未生成接入文件。
- 带skill的独立接入测试：agent生成合法deployment.yaml和workflow，CLI及actionlint通过，平台引用和必填输入符合实际公共工作流，Dockerfile内容未改变。
- 带skill的独立恢复诊断测试：agent识别pending、区分最后成功记录与实际健康状态、选择transaction.from作为恢复目标，并遵守只诊断不执行的范围。

这些测试使用work/下的隔离fixture，未连接GitHub仓库或目标服务器。不能据此宣称真实发布/部署已通过。

## init补充验证（CLI v1.1.0，2026-10-07）

- 全仓测试共43个：42通过，1个目录符号链接测试因Windows权限限制跳过；Linux CI会执行该项。
- init生成合法部署描述、平台引用一致的发布工作流；可选部署工作流和自定义路径通过测试。现有文件不覆盖、冲突前置检查、dry-run不写目录、非法参数及缺失Dockerfile均已验证。
- 自包含CLI及skill内CLI包含init，禁用系统site-packages后可运行；v1.1.0 wheel构建通过。
- 实际生成的发布/部署YAML及更新模板通过actionlint。
- 独立agent使用init完成接入：保留已有release.yml，另建team-release.yml；本地CLI为1.1.0但用户指定的平台引用保持v1.0.0。预览、生成、validate均退出0，未发布或操作服务器。
- 工具版本升级未改变发布包协议或服务端生命周期实现；真实Docker/GitHub/SSH验证状态与前述边界一致。
