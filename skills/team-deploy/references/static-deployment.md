# 静态项目（CLI >=1.13.0）

先定位目标环境中的 ctl/deployctl 并检查 --version。缺失或旧版按 [CLI 安装与升级](cli-lifecycle.md) 检查 PATH、使用随附 assets/deployctl.pyz 或安装器；已受安装器管理的命令可 `ctl self-update --version <实际已发布版本>`。不把源码1.13.0当作正式 tag。静态部署在 Linux 执行，无需 Docker。

用 `ctl whoami`、`ctl project show project-a`、`ctl project-config get project-a --prod` 确认身份/项目类型/目录，不 reveal。平台 deployment_type 缺省 Docker，创建后不可改变。示例组team须替换为实际已授权组。新项目可 `ctl project create project-a --name 'Project A' --group team --deployment-type static`；已有 Docker 项目不能 patch 成 static。publisher 发布、deployer 安装、owner 管理项目组，沿用用户授权范围。

## 接入与制品

静态项目复制/合并随附 `assets/templates/static-release.yml` 到业务 `.github/workflows/release.yml`。本期 init 仍生成 Docker 模板，不对纯静态项目运行 Docker init/validate/package，不编造 Dockerfile、端口或 health。

填写 project/server/build-command/output-directory，可选 test-command。uses 与 platform-ref 使用同一个真实不可变提交或已发布版本；配置 scoped CTL_PUBLISH_TOKEN，私有平台补 PLATFORM_READ_TOKEN 和工作流访问权限。构建脚本自行准备工具依赖，参数交给项目脚本；业务 tag 与工具版本独立。

工作流先按版本/来源 commit 恢复原包，无已登记版本才构建。不同 commit 冲突应修正来源或发新业务版本，不绕过检查重建覆盖。统一 `ctl publish APP --version TAG --package PATH --commit HASH --channel stable`；也可管理台上传安全ZIP/tar.gz，不需要 Registry Token。工作流发布到 ctl 平台，不生成 GitHub Release URL。

包根直接成为公开目录根；dist/index.html 应归档为 index.html，不假设剥离 wrapper。平台仓库工具 `python scripts/package_static.py dist --output delivery/site.tar.gz` 支持 --archive-format zip；输出不得在 dist 内。只含公开文件，禁止 Token/.env/私有配置；压缩<=256MiB、文件总量<=1GiB、单文件<=256MiB、展开条目<=50000，拒绝链接和特殊文件。下载后不再次构建，不执行包内脚本。

## 统一目录部署

远端项目 deployment_defaults.target_dir 覆盖组继承，项目清空恢复继承，有效远端值优先于 CLI 兜底。组目录是固定路径，没有项目名插值；多个项目/不同环境各用专属目录，不可共享同一路径或相互嵌套。

管理台项目「环境配置」填写安装目录并审阅保存，可修改高优先级远端值。CLI也可准备私有权限600文件 `{"deployment_defaults":{"target_dir":"/var/www/project-a"}}`，执行 `ctl project-config apply project-a --prod --file /private/project-a-prod.json`；CLI先读取修订后检查并发保存。新项目须用 `--group <真实已授权组>` 加入实际项目组；不假设组scoped publisher能访问default。

```bash
ctl install project-a --prod --target-dir /var/www/project-a
ctl upgrade project-a --prod --version v1.1.0
ctl rollback project-a --prod
ctl status project-a --prod
ctl logs project-a --prod --tail 50
```

首次目录必须不存在或为空，拒绝未知链接/非空目录/跨 root 所有权冲突。首次保存绑定，升级/回滚沿用，改目录需单独规划迁移。--root 缓存的已有父目录必须供Web用户遍历；不擅自 chmod 外部父目录。

目标为受管理 symlink，升级完整替换文件树，旧文件从公开路径消失，旧包/树保留离线回滚。state/包/清单/诊断不在公开目录；不要手改链接/缓存。备份 state、版本缓存、root/.ctl-static-cache.json 和 target.parent/.ctl-static；状态丢失不应重装接管未知目标。

不用 website 前缀或旧 --release，不传 --env-var/--set/--unset-env/--port/--bind，不执行 stop/restart、pre/post hook 或部署时配置注入；这些参数会明确报错。

pending 时先 rollback 恢复再升级。离线 rollback/status/logs 校验本机绑定/缓存/树，不取最新平台配置。上报失败与部署失败分别报告，说明实际 current/previous/transaction。status=deployed只证明文件安装，logs是部署事件，不代表Nginx/Caddy或URL健康。

Web入口指向 target_dir，由原入口管理TLS/域名/SPA fallback。确认允许符号链接且可遍历缓存父目录；公开文件0644、目录0755。工具不修改/重启入口，不自动清理旧版本缓存。
