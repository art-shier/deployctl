# 管理服务模式（CLI>=1.7.0）

目标服务器先检查CLI>=1.7.0。管理服务支持 `sudo ctl server install --release <真实ctl-platform发布包URL>`，无需clone或先登录；相邻.sha256自动验证，包固定镜像digest。默认origin为https://ctl.shier.art，Registry host为ctl.shier.art。升级使用server upgrade；失败保留pending，修复后重试同包。平台数据/密钥留在/opt/ctl-platform，HTTPS由现有代理配置。引导不提供数据库自动回滚。详见平台仓库docs/control-plane.md。

已注册项目与授权环境可以：

```bash
ctl login --server https://<实际管理服务地址>
ctl install <项目> --prod
ctl upgrade <项目> --env <环境> --version <已发布版本> --port <主机端口>
ctl rollback <项目> --env <环境>
```

交互或私有`--token-file`读取凭据；不要把Token传到命令行、URL、应用容器或hooks。默认`/etc/deployctl/client.json`（目录700、文件600、当前身份所有）；可用`--client-config`指定私有文件。sudo登录和部署使用同一身份。生产主机使用限定项目/项目组与环境的deployer；CI用限定项目/项目组的publisher。owner仅用于管理。

服务端v1.8.1提供项目组详情与组内授权，旧项目自动归入`default`，旧Token仍只授权原项目。新Token只授权一个或多个项目组，一次login即可部署授权范围内的项目；组授权随当前成员变化，旧项目Token只保留兼容，仍不随移组变化。CLI>=1.8.0提供`ctl whoami`与`ctl projects`查看身份及可访问项目，不显示Token。旧CLI1.7仍可使用共享凭据执行安装。

未指定环境时managed install/upgrade取项目默认。`--prod`是`--env prod`，不能与其他环境冲突。版本默认取环境目标，不等于latest。status/rollback等本地操作省略环境，仅在主机唯一已安装环境时允许。

CI先推送托管或已登记外部镜像，取得真实digest，按现有契约package，再`ctl publish <项目> --version <版本> --package <包> --channel stable`。不提供channel不会推进stable。同版本内容不可覆盖。publisher不可读生产配置；不要把构建参数放进环境配置。

公共外部Registry自动匿名认证。外部私有仓库校验使用`publish --registry-token-file <权限600的短期pull Token文件>`；服务端只验证本次登记仓库，不保存/回显/转交主机。该文件不能使用ctl平台Token或长期账号密码。安装主机沿用自己的Docker凭据。

管理台runtime高于本次env-var，本次管理台安装参数高于set；pre回读后仍用同一固定修订，不能依靠hook绕过最高层。删除远端键下次升级回退到本地值，空值保留；保存管理台配置不会自动重启。每组/最终合并128键、键128字符、值4096字符、总64KiB。

托管Registry使用临时指定仓库bearer，ctl拉取后hook中Docker辅助命令用`--pull never`。不要在隔离hook中重新要求平台Token。快照中`.management.json`保存非秘密来源/资源并参与完整性校验；不要手工修改文件。rollback/restart不取最新管理台配置，缓存镜像可离线回滚，缺少镜像仍需Registry可用。

显式`--release`默认走旧方式，无需平台登录，环境必填。`--with-platform-config`需要显式包与平台登记版本一致；`--release`与`--version`冲突。不要为替换镜像改写已受检包，登记新版本。

结果上报失败不代表服务安装失败，待补报结果在私有配置目录的`receipts/`。报告实际状态和事务结果，不能把安装记录当作实时在线监控。具体部署/发布授权沿用用户范围；不修改生产服务的密钥或数据库。

管理台默认进入项目组，点击组查看组内项目；组授权页可固定当前组创建发布/部署凭据，项目页不提供凭据。POST /tokens 必须传 groups，拒绝 project/projects 授权。组成员调整支持 expected_group 并发检查，冲突重新加载；不要绕过管理API直接改数据库。
