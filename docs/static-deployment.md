# 静态文件部署（CLI 1.13.0）

静态与 Docker 项目共享 `publish/install/upgrade/rollback/status/logs`。平台的 `deployment_type` 决定分发，旧项目缺省 Docker；创建后类型固定。示例组team需替换为实际已授权组。静态路径需要配套新版管理服务和 Linux/Python >=3.10，无需 Docker。

## GitHub 项目接入

1. 管理台注册项目时选择「静态文件」，或用有权限的凭据执行 `ctl project create project-a --name 'Project A' --group team --deployment-type static`。
2. 将 [静态模板](../templates/static-release.yml) 复制为业务 `.github/workflows/release.yml`，填写 project、server、build-command、output-directory 和可选 test-command。`uses` 与 `platform-ref` 指向同一个包含静态功能的实际不可变提交或已发布版本。模板的 v1.13.0 是本功能版本，正式发布前换成真实提交；源码版本不代表已发布 tag。
3. 业务仓库添加限定项目/组的 publisher 凭据 `CTL_PUBLISH_TOKEN`。私有平台额外添加 `PLATFORM_READ_TOKEN` 并开放 reusable workflow 调用权限。
4. 推送业务自己的 tag，如 v1.0.0。工作流先恢复已登记同版本/同来源提交的原包，无版本才测试、构建、打包并 `ctl publish --commit ... --channel stable`。不同来源冲突不能绕过检查重建覆盖。

构建命令自行准备工具和依赖；普通参数由项目定义，例如 `npm ci && BUILD_PROFILE=production npm run build`。主机不再次构建。工作流发布到 ctl 平台，输出 application/version/sha256/archive_format，不生成 GitHub Release 下载地址。本期 `ctl init` 仍生成 Docker 模板，纯静态项目直接使用独立模板。

## 包根目录与本地发布

支持 ZIP/tar.gz；包根就是公开目录根，不自动剥离 wrapper。`dist/index.html` 应归档为 `index.html`，而非 `dist/index.html`。在平台仓库使用确定性打包工具：

```bash
python scripts/package_static.py ./dist --output ./delivery/project-a-v1.0.0.tar.gz
ctl publish project-a --version v1.0.0 --package ./delivery/project-a-v1.0.0.tar.gz \
  --commit "$(git rev-parse HEAD)" --channel stable
```

工具固定顺序/权限/时间，支持 `--archive-format zip`，输出不能在产物目录内。也可直接上传已生成的安全包，不要求 Docker release.yaml/Compose，不执行包内脚本。上限：压缩 256MiB、文件总量 1GiB、单文件 256MiB、展开文件/目录 50000、UTF-8 路径 4096 字节。拒绝链接、穿越、重名、前缀冲突、特殊文件、加密和损坏包。

公开目录只含公开产物，切勿包含 Token、.env 或私有配置。普通文件为0644、目录为0755。包、状态、清单与日志放在缓存，不放入公开目录。

## 安装目录与统一命令

管理台环境配置可设置目标目录/版本；首次安装也可传 CLI 兜底：

```bash
ctl install project-a --prod --target-dir /var/www/project-a
ctl upgrade project-a --prod --version v1.1.0
ctl rollback project-a --prod
ctl status project-a --prod
ctl logs project-a --prod --tail 50
```

生产主机使用限定项目/环境的 deployer。项目目录覆盖组继承，清空恢复继承；有效远端目录优先于 CLI 兜底。首次必须有目录，后续沿用已保存的主机绑定，改目录会拒绝，迁移需单独规划。

组默认目录是**固定绝对路径**，不会插入项目名。同组多项目/不同环境需分别指定项目专用目录，如 `/var/www/project-a/prod` 和 `/var/www/project-b/prod`；不得共享或相互嵌套。仅接管不存在/空目录，拒绝未知链接、已有非空目录和所有权冲突。目标与缓存已有父目录需可公开遍历，ctl 不修改外部父目录权限。

公开路径为 ctl 管理的符号链接。升级切换完整版本树，旧文件从公开路径消失，旧版本保留本机供离线回滚。不要修改链接/缓存。主机静态变更串行执行，遇到锁冲突重试。备份同时保留 state、版本缓存与 target.parent 的 `.ctl-static` 所有权记录。

静态项目拒绝 `--env-var/--set/--unset-env/--port/--bind`、stop/restart 和旧 `--release`。本期不注入浏览器运行变量、不执行 pre/post hook；公开配置在构建时生成。

## Web 入口和恢复

Nginx/Caddy、域名、TLS、SPA fallback 和访问日志由原入口管理，例如 Nginx：

```nginx
server {
    listen 80;
    server_name example.com;
    root /var/www/project-a;
    location / { try_files $uri $uri/ =404; }
}
```

SPA 可按项目需要改末项为 `/index.html`。Web 用户需能遍历缓存父目录，允许读取符号链接；检查 Nginx 禁止链接和文件缓存策略。ctl 不安装/重启入口，不证明 URL 在线。status 显示本机部署版本/目录/事务，logs 是部署事件。

失败自动恢复原树，恢复失败保留 pending；先 rollback 再升级。平台断网时 rollback/status/logs 仍可用，缓存或树篡改会被拒绝。回执上报失败不代表文件安装失败，回执也不是实时健康监控。
