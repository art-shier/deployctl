# 管理服务模式（CLI>=1.7.0）

随附1.7.0尚未发布；目标服务器先检查版本，不用不存在的版本标签安装。管理服务API和Registry单独引导，平台Compose脚本位于源码`control-deploy/bootstrap.sh`；详细运维在源码`docs/control-plane.md`。

已注册项目与授权环境可以：

```bash
ctl login --server https://<实际管理服务地址>
ctl install <项目> --prod
ctl upgrade <项目> --env <环境> --version <已发布版本> --port <主机端口>
ctl rollback <项目> --env <环境>
```

交互或私有`--token-file`读取凭据；不要把Token传到命令行、URL、应用容器或hooks。默认`/etc/deployctl/client.json`（目录700、文件600、当前身份所有）；可用`--client-config`指定私有文件。sudo登录和部署使用同一身份。生产主机使用project/env限定的deployer；CI用project限定的publisher。owner仅用于管理。

未指定环境时managed install/upgrade取项目默认。`--prod`是`--env prod`，不能与其他环境冲突。版本默认取环境目标，不等于latest。status/rollback等本地操作省略环境，仅在主机唯一已安装环境时允许。

CI先推送托管或已登记外部镜像，取得真实digest，按现有契约package，再`ctl publish <项目> --version <版本> --package <包> --channel stable`。不提供channel不会推进stable。同版本内容不可覆盖。publisher不可读生产配置；不要把构建参数放进环境配置。

管理台runtime高于本次env-var，本次管理台安装参数高于set；pre回读后仍用同一固定修订，不能依靠hook绕过最高层。删除远端键下次升级回退到本地值，空值保留；保存管理台配置不会自动重启。每组/最终合并128键、键128字符、值4096字符、总64KiB。

托管Registry使用临时指定仓库bearer，ctl拉取后hook中Docker辅助命令用`--pull never`。不要在隔离hook中重新要求平台Token。快照中`.management.json`保存非秘密来源/资源并参与完整性校验；不要手工修改文件。rollback/restart不取最新管理台配置，缓存镜像可离线回滚，缺少镜像仍需Registry可用。

显式`--release`默认走旧方式，无需平台登录，环境必填。`--with-platform-config`需要显式包与平台登记版本一致；`--release`与`--version`冲突。不要为替换镜像改写已受检包，登记新版本。

结果上报失败不代表服务安装失败，待补报结果在私有配置目录的`receipts/`。报告实际状态和事务结果，不能把安装记录当作实时在线监控。具体部署/发布授权沿用用户范围；不修改生产服务的密钥或数据库。
