# deployctl：团队标准部署

业务项目通过 GitHub Actions 构建 Docker 镜像和标准发布包，Linux 云服务器用 `ctl install/upgrade/rollback` 统一部署。`ctl` 与 `deployctl` 是同一个工具。

平台仓库：[art-shier/deployctl](https://github.com/art-shier/deployctl) · [版本与安装资产](https://github.com/art-shier/deployctl/releases) · [构建流水线](https://github.com/art-shier/deployctl/actions)

支持单个无状态 HTTP 服务、Docker Compose、私有 GitHub Release 下载、升级失败恢复、人工回滚和状态/日志查询。单机更新可能短暂中断；数据库迁移、多机滚动发布、HTTPS 入口和持久化存储需另行配置。

## 一键安装 ctl

仓库目前是私有的。先通过 `gh auth login --hostname github.com` 登录有仓库读取权限的账户，然后在 Linux/Bash 中运行：

```bash
set -o pipefail
gh api --hostname github.com 'repos/art-shier/deployctl/contents/install.sh?ref=v1.3.0' \
  -H 'Accept: application/vnd.github.raw+json' | bash -s -- --user --version v1.3.0
export PATH="$HOME/.local/bin:$PATH"
ctl --version
```

安装器下载 CLI、检查 SHA256 和版本，安装 `ctl` 与 `deployctl` 到 `~/.local/bin`。CLI 内置依赖，无需服务器 pip 安装。再次执行可升级本安装器管理的命令；已有其他同名工具会保留并报告冲突。

安装后，CLI自身升级使用`ctl self-update`，指定工具版本可用`ctl self-update --version v1.3.0`。命令沿用原安装目录、平台仓库和别名选择。v1.2.0及更早版本没有此命令，需要先重新执行安装器升级一次。详见 [工具定位与升级](skills/team-deploy/references/cli-lifecycle.md)。

要求 Python >=3.10；服务部署另需 Docker Engine、Docker Compose >=2.30。安装脚本不自动安装系统组件或提升权限。无 `gh`、指定安装目录、升级最新版等用法见 [安装说明](docs/install.md)。

## 业务项目接入

在已有 Dockerfile 的业务仓库根目录执行：

```bash
ctl init project-a \
  --platform-repository art-shier/deployctl --platform-ref v1.3.0 \
  --private-platform --port 8080 --health-path /health/ready
ctl validate deploy/deployment.yaml
```

生成 `deploy/deployment.yaml` 和 `.github/workflows/release.yml`。填写服务的实际端口、就绪接口及配置要求；在业务仓库配置有平台代码读取权限的 `PLATFORM_READ_TOKEN`。平台 Actions 的私有工作流访问范围也需允许这些业务仓库调用，见 [流水线说明](docs/release-pipeline.md)。

提交接入文件，再推送业务项目自己的版本标签，如 `v1.0.0`，即可生成镜像 digest、`project-a-v1.0.0.tar.gz` 和相邻 `.sha256`。平台工具版本与业务版本独立。

`--dry-run` 可预览生成内容；`--with-deploy-workflow` 可生成可选 SSH 部署入口。已有文件不会覆盖，详见 [init](docs/init.md) 和 [项目接入手册](docs/onboarding.md)。

## 服务器部署

按 [运维手册](docs/operations.md) 配置部署用户、Docker 镜像拉取权限以及 `/etc/deployctl/project-a/production/{config.env,secrets.env}`，然后：

```bash
ctl install project-a --env production --release <项目Release中的tar.gz地址>
ctl status project-a --env production
ctl upgrade project-a --env production --release <新版本tar.gz地址>
ctl rollback project-a --env production
```

工具会自动下载发布包及校验文件。私有业务 Release 通过环境变量 `GH_TOKEN` 提供只读下载凭证；不要把 Token 填进 URL。服务器镜像拉取凭证独立配置。

## 命令与交付物

| 命令 | 用途 |
|---|---|
| `init` | 生成项目接入 YAML |
| `self-update` | 更新当前安装的部署工具自身（CLI>=1.3.0） |
| `validate` / `package` | 校验部署描述 / 生成标准发布包 |
| `install` / `upgrade` / `rollback` | 首次部署 / 更新 / 回滚 |
| `status` / `logs` | 查看状态 / 日志 |
| `restart` / `stop` | 重启 / 停止 |

| 文件/目录 | 用途 |
|---|---|
| `install.sh` | 自包含安装入口，和self-update共用 `deployctl/bootstrap.py` |
| `.github/workflows/build-release.yml` | 业务项目复用的构建交付流水线 |
| `.github/workflows/deploy.yml` | 可选 SSH 部署流水线 |
| `.github/workflows/platform-release.yml` | CLI 检查、构建、草稿验证及正式发布 |
| `templates/` / `schemas/` | 项目模板 / 编辑器 Schema |
| `examples/project-a/` | HTTP 示例项目 |
| `skills/team-deploy/` | Agent skill，附 CLI、模板及操作指南 |

平台 Release 提供自包含 `deployctl.pyz`、wheel、`install.sh`、skill ZIP 及 SHA256。业务发布包只包含 `release.yaml`、`compose.yaml`、`.env.example` 和 `README.md`；生产密钥留在服务器。

需要 Agent 接入项目或操作服务时，使用 [Team Deploy skill](skills/team-deploy/SKILL.md)，安装方法见 [Agent 使用说明](docs/agent-usage.md)。

## 开发与检查

```bash
python -m pip install -e .
python scripts/build_install_script.py --check
python scripts/build_release_assets.py
python -m unittest discover -s tests -v
python -I -S dist/deployctl.pyz validate templates/deployment.yaml
bash scripts/installer_smoke.sh
# Linux，需 Docker 与网络
python scripts/docker_integration.py
```

CI 在 Linux Python 3.10/3.12 和 Windows Python 3.12 检查 CLI；Linux 另跑真实安装和 Docker 生命周期。版本发布先验证草稿 Release 中的真实资产，再公开已验证的版本。详见 [构建与发布](docs/release-pipeline.md)、[验证记录](docs/verification.md)。

`init/validate/package` 可在 Windows 执行，服务运行命令面向 Linux。固定镜像 digest；版本不覆盖；回滚不恢复环境配置或数据库。默认仅监听 `127.0.0.1`，外部访问通过服务器网关或显式配置的绑定地址。
