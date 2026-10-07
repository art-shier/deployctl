# 验证记录

## v1.3.0工具自更新与skill补充（2026-10-07）

- 新增self-update，复用标准库安装器，沿用安装目录、平台仓库、自定义命令名和别名选择；旧版安装记录兼容。工具更新不调用服务运行管理器。
- 全仓本地测试67项：65通过，2项因Windows环境跳过；新用例覆盖两入口更新、自定义目录/命令名、无别名、旧安装记录、非受管理入口、命令被修改、冲突、坏校验、写入失败恢复及验证超时。
- [main CI](https://github.com/art-shier/deployctl/actions/runs/37596193551) 与 [v1.3.0发布流水线](https://github.com/art-shier/deployctl/actions/runs/37596325046) 全部通过，Linux完整运行测试，并执行真实Docker、安装器、一键入口和self-update。
- 发布前从真实草稿资产执行指定版本self-update；发布后通过PATH调用ctl self-update解析latest，两个命令均验证为1.3.0。
- [v1.3.0 Release](https://github.com/art-shier/deployctl/releases/tag/v1.3.0) 已正式发布，latest解析为v1.3.0。再次下载的全部资产SHA256与总清单匹配。
- skill-creator格式校验通过；独立Agent仅凭skill及引用即可处理Linux PATH、私有在线/离线安装、v1.2.0重新安装、v1.3.0自更新及Windows本地接入，无需阅读安装器源码。
- 独立代码复核未发现待修正的问题；补充检查验证了独立摘要不匹配、安装记录写入失败恢复，以及旧版自定义命令名/无别名兼容。
- 本地已安装skill同步到正式v1.3.0分发资源，保留原UI metadata；隔离Python环境下版本、self-update帮助和CLI校验和均通过。

本次没有操作生产业务服务。下面保留先前版本的验证记录。

## v1.2.0正式发布与安装验证（2026-10-07）

- [更新Actions后的main CI](https://github.com/art-shier/deployctl/actions/runs/37589710522) 和 [v1.2.0发布流水线](https://github.com/art-shier/deployctl/actions/runs/37589829877) 全部通过，包括Linux/Windows检查、真实Docker与CLI安装。
- [v1.2.0 Release](https://github.com/art-shier/deployctl/releases/tag/v1.2.0) 已正式发布，latest解析为v1.2.0；共9个资产，含CLI、installer、skill ZIP、wheel及校验文件。
- 发布前通过草稿ID从真实GitHub API下载CLI，并校验/安装两个命令；GitHub Contents API取得的安装脚本一键入口也成功执行。
- 发布后通过正式标签再次下载、安装并运行ctl，实际版本为1.2.0。
- 本地重新下载全部正式Release资产，4个独立SHA256和总清单均匹配；已安装skill同步到正式分发资源，隔离环境下CLI报告1.2.0。

本记录仅补充文档；发布标签指向的代码和Release资产保持不变。尚未执行真实云服务器SSH部署或生产验收。

## 平台接入验证（CLI v1.2.0，2026-10-07）

- 平台代码已推送到私有仓库art-shier/deployctl，首轮 [真实GitHub CI](https://github.com/art-shier/deployctl/actions/runs/37589523486) 全部通过。
- Linux Python3.10/3.12完整运行55项测试，包括目录符号链接与带空格的自定义Python解释器安装；Windows Python3.12完成适用测试，Linux专用启动和目录链接权限相关项按环境跳过。
- Linux实际执行安装器：两个命令、重复安装、坏校验拒绝、init和validate通过。
- 真实Docker集成通过：容器安装、升级、坏版本恢复、人工回滚、raw环境变量、状态/日志、停止和重启。
- 本地wheel、自包含CLI、生成安装脚本一致性、actionlint及Bash语法通过。
- 安装器固定github.com凭证查询，下载资产跨域重定向移除认证，检查CLI版本与SHA256；Release流程先验证草稿真实资产再发布。
- Actions升级为已核实存在的checkout v7.0.1、setup-python v7.0.0、upload-artifact v7.0.1；Linux Runner固定Ubuntu24.04。版本标签的发布流程将再次验证所发布代码及真实GitHub下载入口。

以下为早期本地开发记录，当时尚未执行的GitHub和Docker验证现已由上面的CI补齐。SSH云服务器和生产环境验收仍需要实际目标，当前没有操作生产服务器。

## 早期本地检查（2026-10-04）

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

## 早期尚未执行（截至2026-10-04）

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
