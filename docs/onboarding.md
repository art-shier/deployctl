# 项目接入手册

平台仓库为 `art-shier/deployctl`，当前是私有仓库。以下业务仓库 `acme/project-a` 为示例，请替换成你的实际项目名称。

## 1. 配置平台访问（团队做一次）

平台仓库需在 Actions 设置中允许目标业务仓库访问 reusable workflows，并给业务仓库配置能读取平台代码的 `PLATFORM_READ_TOKEN`。业务仓库自己的 `GITHUB_TOKEN` 通常不能 checkout 另一个私有仓库。

当前平台使用v1.4.0；升级时同步CLI、pyproject版本、模板引用和skill资源，再通过 [平台发布流水线](release-pipeline.md) 发布新标签。发布包仍使用schema_version: 1和minimum_deployctl_version=1.0.0，业务项目版本与平台版本独立。

## 2. 初始化服务器（每台做一次）

服务器要求 Linux、Python >=3.10、Docker Engine、Compose >=2.30，且能访问镜像仓库。按云厂商或 Docker 官方方式安装运行环境；本工具不代替其安装管理。

先登录有平台读取权限的GitHub账户，一键安装CLI：

```bash
gh auth login --hostname github.com
set -o pipefail
gh api --hostname github.com 'repos/art-shier/deployctl/contents/install.sh?ref=v1.4.0' \
  -H 'Accept: application/vnd.github.raw+json' | bash -s -- --user --version v1.4.0
export PATH="$HOME/.local/bin:$PATH"
ctl --version
```

选择专用部署用户，例如 `deploy`，确保其有写部署目录和操作 Docker 的权限：

```bash
sudo install -d -o deploy -g deploy -m 0750 /opt/deployments
sudo install -d -o deploy -g deploy -m 0750 /etc/deployctl
```

Docker 操作权限接近主机管理员权限，只授予可信部署身份。不同项目共享这个身份适用于同一可信团队，不构成租户隔离。

如镜像私有，在**实际执行 deployctl 的身份下**使用只读拉取凭证登录镜像仓库：

```bash
docker login ghcr.io --username <有读取权限的用户名>
```

按提示输入 Token。GitHub 的 GHCR Token/包访问权限和 GitHub Release 下载权限分别配置；组织 SSO 也可能需要授权。

## 3. 项目添加接入文件

已有Dockerfile的项目可直接执行init生成下面的接入文件；手工复制方式仍适用于已有配置需要合并的情况：

```bash
ctl init project-a --platform-repository art-shier/deployctl \
  --platform-ref v1.4.0 --private-platform --port 8080 --health-path /health/ready
```

引用填写平台实际可用版本。加--dry-run预览；已有文件会保留并报告冲突。详见 [init说明](init.md)。

项目已有 Dockerfile 就保留，否则参考 `examples/project-a/Dockerfile` 创建。模板文件复制位置：

```text
templates/deployment.yaml → project-a/deploy/deployment.yaml
templates/release.yml → project-a/.github/workflows/release.yml
```

修改 application、容器端口、健康检查、资源上限、required_config。health_path 必须是无需认证的就绪接口，且部署身份可以从服务器访问它。

构建参数不必在init时填写。项目需要时自行添加build.args，并通过调用工作流的build-args覆盖；Dockerfile在使用参数的构建stage声明ARG，详见 [构建参数](build-args.md)。

修改工作流：

```yaml
jobs:
  release:
    uses: art-shier/deployctl/.github/workflows/build-release.yml@v1.4.0
    with:
      platform-repository: art-shier/deployctl
      platform-ref: v1.4.0
      deployment-file: deploy/deployment.yaml
      version: ${{ github.ref_name }}
    secrets:
      PLATFORM_READ_TOKEN: ${{ secrets.PLATFORM_READ_TOKEN }}
```

调用引用和 platform-ref 要保持一致。构建默认为 linux/amd64；ARM 服务器设置 `platforms: linux/arm64`，或 `linux/amd64,linux/arm64`。测试命令通过 test-command 明确接入；未填写时平台不会凭空知道项目如何测试。

默认镜像地址是 `ghcr.io/<项目owner>/<项目repo>`。也可提供 `registry` 和 `image-name`；非 GHCR 仓库通过 secrets 显式映射 REGISTRY_USERNAME、REGISTRY_TOKEN。若平台仓库私有，映射 PLATFORM_READ_TOKEN。

## 4. 项目发布版本

```bash
git add Dockerfile deploy .github
git commit -m "Integrate team deployment workflow"
git tag v1.0.0
git push origin HEAD
git push origin v1.0.0
```

工作流成功后，项目 Releases → v1.0.0 → Assets 中会出现：

```text
project-a-v1.0.0.tar.gz
project-a-v1.0.0.tar.gz.sha256
```

URL：`https://github.com/acme/project-a/releases/download/v1.0.0/project-a-v1.0.0.tar.gz`。流水线也将 URL、SHA256、application、image 暴露为 reusable workflow outputs，供下游部署 job 使用。

同一版本不可重新上传不同内容。如果发布包已经存在，工作流会失败，而不会覆盖它。

## 5. 填写服务器配置

以部署身份创建目录和配置文件：

```bash
install -d -m 0700 /etc/deployctl/project-a/production
install -m 0600 /dev/null /etc/deployctl/project-a/production/config.env
install -m 0600 /dev/null /etc/deployctl/project-a/production/secrets.env
```

编辑 config.env 放一般配置；secrets.env 放数据库密码和密钥。格式为 raw `KEY=value`：

```dotenv
DATABASE_URL=postgresql://user:password@db.internal:5432/app
APP_SECRET=some$literal#secret
```

不要套 shell 引号、不要写 `export`、不支持多行值。`$` 和值内 `#` 保留原文。配置重名时 secrets.env 覆盖 config.env；同一文件内重名拒绝。APP_VERSION 由平台注入。

已有文件不要再执行会清空文件的 install 命令。首次安装若发现文件缺失，工具会创建空文件并指出必需项，填完后重试。

## 6. 首次安装和后续升级

```bash
deployctl install project-a --env production \
  --release https://github.com/acme/project-a/releases/download/v1.0.0/project-a-v1.0.0.tar.gz

deployctl status project-a --env production
curl http://127.0.0.1:8080/health/ready

deployctl upgrade project-a --env production \
  --release https://github.com/acme/project-a/releases/download/v1.1.0/project-a-v1.1.0.tar.gz
```

默认自动下载相邻 `.sha256`。本地文件安装也要一起下载校验文件；可用 `--sha256 <期望摘要>` 代替相邻校验文件。

私有 GitHub 项目下载时，使用只读 Release Token：

```bash
read -rsp 'GitHub read token: ' GH_TOKEN; echo
export GH_TOKEN
# 在同一 shell 执行 install/upgrade
unset GH_TOKEN
```

工具通过 GitHub API 解析私有资产，重定向到外部存储时去掉 Authorization。不要把 Token 放进 URL。云内网访问不稳定时，可先在可访问 GitHub 的环境下载发布包并上传服务器。

多个应用共用服务器时，给每个服务分配不同 host_port，或者首次 install 使用 `--port 18080`。首次绑定会记录并在升级中保留。域名、TLS、反向代理和安全组由服务器入口层配置；默认只监听本机。

## 7. 可选：GitHub 自动部署

复制 `templates/deploy.yml` 到项目 `.github/workflows/deploy.yml`，替换仓库和 application。先采用手动 workflow_dispatch，输入现有发布包 URL 和 SHA256，选 staging/production 和 install/upgrade。

配置 GitHub Environment 的审批规则，以及 SSH_HOST、SSH_USER、SSH_PRIVATE_KEY、SSH_KNOWN_HOSTS。不同环境需要隔离凭证；若使用 Environment secrets，GitHub 在被调用 workflow 的 environment job 内提供对应秘密。平台私有时额外配置 PLATFORM_READ_TOKEN。

SSH_KNOWN_HOSTS 从可信渠道核对服务器主机指纹后填入；非 22 端口的记录通常为 `[host]:port`。流水线不会禁用 StrictHostKeyChecking。

该流水线在 Runner 上校验并下载私有发布包，再通过 SCP 传入服务器。服务器仍需配置私有镜像拉取凭证，但不需要持有项目 Release Token。服务器预先安装 deployctl，SSH 身份应能写默认目录并操作 Docker；流水线不会自动执行 sudo 或安装系统组件。

生产按审批规则放行后，调用同一个 install/upgrade。首次安装成功后，之后选择 upgrade。内网服务器可通过自托管部署 Runner 接入，在调用时提供 `runner: <部署Runner标签>`。

需要合并构建和部署时，在项目 release 工作流增加依赖 release job 的 reusable deploy job，将 `needs.release.outputs.release_url` 和 `.sha256` 传入，保留环境审批。
