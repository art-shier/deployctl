# 管理服务模式（CLI>=1.7.0）

目标服务器先检查CLI>=1.7.0。管理服务支持 `sudo ctl server install --release <真实ctl-platform发布包URL>`，无需clone或先登录；相邻.sha256自动验证，包固定镜像digest。默认origin为https://ctl.shier.art，Registry host为ctl.shier.art。升级使用server upgrade；失败保留pending，修复后重试同包。平台数据/密钥留在/opt/ctl-platform，HTTPS由现有代理配置。引导不提供数据库自动回滚。详见平台仓库docs/control-plane.md。

已注册项目与授权环境可以：

```bash
ctl login
ctl install <项目> --prod
ctl upgrade <项目> --env <环境> --version <已发布版本> --port <主机端口>
ctl rollback <项目> --env <环境>
```

CLI>=1.8.2默认管理地址为`https://ctl.shier.art`，已有自定义配置继续沿用。`ctl config get server`查看，`ctl config set server <HTTPS地址或域名>`修改；修改到不同服务清除旧登录，随后执行`ctl login`。所有命令使用同一身份及`--client-config`；旧`login --server`仅保留兼容。交互或私有`--token-file`读取凭据；不要把Token传到命令行、URL、应用容器或hooks。CLI>=1.8.3默认`~/.ctl/client.json`（目录700、文件600、当前身份所有）；可用`--client-config`指定私有文件。普通用户登录无需sudo；使用sudo时是root的独立配置。登录和部署使用同一身份。新位置缺失时只复制当前用户拥有且符合旧隐私权限的旧系统登录，保留原文件、新位置优先；不读取其他用户或不安全的旧配置。生产主机使用限定项目/项目组与环境的deployer；CI用限定项目/项目组的publisher。owner仅用于管理。

服务端v1.8.1提供项目组详情与组内授权，旧项目自动归入`default`，旧Token仍只授权原项目。新Token只授权一个或多个项目组，一次login即可部署授权范围内的项目；组授权随当前成员变化，旧项目Token只保留兼容，仍不随移组变化。CLI>=1.8.0提供`ctl whoami`与`ctl projects`查看身份及可访问项目，不显示Token。旧CLI1.7仍可使用共享凭据执行安装。

未指定环境时managed install/upgrade取项目默认。`--prod`是`--env prod`，不能与其他环境冲突。版本默认取环境目标，不等于latest。status/rollback等本地操作省略环境，仅在主机唯一已安装环境时允许。

CI先推送托管或已登记外部镜像，取得真实digest，按现有契约package，再`ctl publish <项目> --version <版本> --package <包> --channel stable`。不提供channel不会推进stable。同版本内容不可覆盖。publisher不可读生产配置；不要把构建参数放进环境配置。

公共外部Registry自动匿名认证。外部私有仓库校验使用`publish --registry-token-file <权限600的短期pull Token文件>`；服务端只验证本次登记仓库，不保存/回显/转交主机。该文件不能使用ctl平台Token或长期账号密码。安装主机沿用自己的Docker凭据。

管理台runtime高于本次env-var，本次管理台安装参数高于set；pre回读后仍用同一固定修订，不能依靠hook绕过最高层。删除远端键下次升级回退到本地值，空值保留；保存管理台配置不会自动重启。每组/最终合并128键、键128字符、值4096字符、总64KiB。

托管Registry使用临时指定仓库bearer，ctl拉取后hook中Docker辅助命令用`--pull never`。不要在隔离hook中重新要求平台Token。快照中`.management.json`保存非秘密来源/资源并参与完整性校验；不要手工修改文件。rollback/restart不取最新管理台配置，缓存镜像可离线回滚，缺少镜像仍需Registry可用。

显式`--release`默认走旧方式，无需平台登录，环境必填。`--with-platform-config`需要显式包与平台登记版本一致；`--release`与`--version`冲突。不要为替换镜像改写已受检包，登记新版本。

结果上报失败不代表服务安装失败，待补报结果在私有配置目录的`receipts/`。报告实际状态和事务结果，不能把安装记录当作实时在线监控。具体部署/发布授权沿用用户范围；不修改生产服务的密钥或数据库。

管理台默认进入项目组，点击组查看组内项目；组授权页可固定当前组创建发布/部署凭据，项目页不提供凭据。POST /tokens 必须传 groups，拒绝 project/projects 授权。组成员调整支持 expected_group 并发检查，冲突重新加载；不要绕过管理API直接改数据库。

CLI>=1.9.0的install/upgrade实时显示Compose镜像层的下载/解压大小、百分比和完成/缓存状态；只有Docker返回总大小时才显示该层百分比，不估算整体安装进度。发布包下载也显示实际字节数与已知长度百分比。长时间无进度时另行显示等待提示。stdout结果不变，--quiet仅关闭显示，仍使用同一有界读取与中断/超时进程组清理逻辑。失败/中断不显示done，恢复成功仍保持失败退出码。只呈现白名单状态和计数，不回显原始Docker/hook日志或配置值。已运行的旧CLI进程不会获得新进度，不重复启动install。

管理服务0.3.0支持项目组按环境配置runtime_env与install_params；项目同名值覆盖组值，移组/组修改仅影响下次部署。配置获取在同一数据库快照内固定成员归属与两级修订，resolve协议保持兼容。凭据可编辑groups/projects/excluded_projects范围，排除项始终优先；Token加密保存、owner可单独查看，旧哈希凭据需确认重新生成；已撤销凭据不再列出，审计保留。
