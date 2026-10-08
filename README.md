# deployctl：团队标准部署

业务项目通过 GitHub Actions 构建 Docker 镜像和标准发布包，Linux 云服务器用 `ctl install/upgrade/rollback` 统一部署。`ctl` 与 `deployctl` 是同一个工具。

平台仓库：[art-shier/deployctl](https://github.com/art-shier/deployctl) · [版本与安装资产](https://github.com/art-shier/deployctl/releases) · [构建流水线](https://github.com/art-shier/deployctl/actions)

本版本包含**管理服务与管理台0.2.1及CLI1.8.3**：注册项目、托管镜像/发布包、管理环境和访问凭据；服务器登录后可直接 `ctl install notes --prod`。管理服务支持 `ctl server install --release <服务端发布包URL>`，无需clone源码。原业务 `--release` 安装方式保留。安装、CI接入、配置优先级和备份见 [管理服务说明](docs/control-plane.md)。

v1.8.1完善**项目组工作台与组授权**：登录先进入项目组，点组查看项目列表，在组内注册、加入或移出项目，并直接管理该组的发布/部署凭据。新凭据只授权项目组，项目详情不再提供凭据入口。旧 Token 保留原项目权限，owner 为全局超级管理员。CLI 提供 `whoami` / `projects`。详见 [项目组和共享部署凭据](docs/control-plane.md#项目组和共享部署凭据v181)。

支持单个无状态 HTTP 服务、Docker Compose、私有 GitHub Release 下载、升级失败恢复、人工回滚和状态/日志查询。单机更新可能短暂中断；数据库迁移、多机滚动发布、HTTPS 入口和持久化存储需另行配置。

v1.8.2简化登录：`ctl login` 默认连接 `https://ctl.shier.art`。可用 `ctl config set server <地址>` 修改默认服务，`ctl config get server` 查看当前地址；切换服务后重新登录。管理服务无需为此升级。

v1.8.3将CLI默认配置移到当前用户的 `~/.ctl/client.json`，自动创建私有目录。用户安装可直接执行 `ctl login`，无需sudo、指定路径或手动chmod。符合原权限要求、属于当前用户的旧 `/etc/deployctl/client.json` 会自动复制到新位置，原文件保留；新配置优先。业务服务的运行配置仍使用原目录。

## 一键安装 ctl

公开仓库可直接在 Linux/Bash 中安装已发布版本：

```bash
set -o pipefail
curl --fail --silent --show-error https://raw.githubusercontent.com/art-shier/deployctl/v1.8.3/install.sh \
  | bash -s -- --user --version v1.8.3
export PATH="$HOME/.local/bin:$PATH"
ctl --version
```

安装器下载 CLI、检查 SHA256 和版本，安装 `ctl` 与 `deployctl` 到 `~/.local/bin`。CLI 内置依赖，无需服务器 pip 安装。再次执行可升级本安装器管理的命令；已有其他同名工具会保留并报告冲突。

安装后，CLI自身升级使用`ctl self-update`，指定工具版本可用`ctl self-update --version v1.8.3`。命令沿用原安装目录、平台仓库和别名选择。v1.2.0及更早版本没有此命令，需要先重新执行安装器升级一次。详见 [工具定位与升级](skills/team-deploy/references/cli-lifecycle.md)。

要求 Python >=3.10；服务部署另需 Docker Engine、Docker Compose >=2.30。安装脚本不自动安装系统组件或提升权限。无 `gh`、指定安装目录、升级最新版等用法见 [安装说明](docs/install.md)。

## 业务项目接入

在已有 Dockerfile 的业务仓库根目录执行：

```bash
ctl init project-a \
  --platform-repository art-shier/deployctl --platform-ref v1.5.0 \
  --private-platform --port 8080 --health-path /health/ready
ctl validate deploy/deployment.yaml
```

生成 `deploy/deployment.yaml` 和 `.github/workflows/release.yml`。填写服务的实际端口、就绪接口及配置要求；在业务仓库配置有平台代码读取权限的 `PLATFORM_READ_TOKEN`。平台 Actions 的私有工作流访问范围也需允许这些业务仓库调用，见 [流水线说明](docs/release-pipeline.md)。

提交接入文件，再推送业务项目自己的版本标签，如 `v1.0.0`，即可生成镜像 digest、`project-a-v1.0.0.tar.gz` 和相邻 `.sha256`。平台工具版本与业务版本独立。

`--dry-run` 可预览生成内容；`--with-deploy-workflow` 可生成可选 SSH 部署入口。已有文件不会覆盖，详见 [init](docs/init.md) 和 [项目接入手册](docs/onboarding.md)。

v1.4.0新增构建参数：init继续生成基础配置，项目后续可在deployment.yaml添加build.args，并通过release.yml的build-args逐次覆盖。参数交给Dockerfile ARG和项目自己的脚本，详见 [构建参数](docs/build-args.md)。

v1.6.0起增加可选pre `refresh_config: true`，让钩子生成的服务器配置在启动前重新加载到新快照；普通钩子行为不变。此新字段要求ctl>=1.6.0，旧v1.5.0不支持。

v1.5.0新增 `--env-var` 应用运行配置、`--set` 安装参数、`--unset-env` 删除覆盖值，以及可选宿主机pre/post hooks。应用通过 `DEPLOYCTL_ENV_FILE` 读取只读 `.env.json`；成功配置随版本提交，失败恢复旧快照。项目接入见 [运行时配置与钩子](docs/runtime-config.md)。

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
| `config get/set server` | 查看或修改默认管理服务地址（CLI>=1.8.2） |
| `login` | 输入Token登录默认管理服务 |
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

平台 Release 提供自包含 `deployctl.pyz`、wheel、`install.sh`、skill ZIP 及 SHA256。业务发布包无hooks时包含原始四文件；声明hooks时使用协议v2并附校验过的脚本，最低ctl1.5.0。生产密钥留在服务器。

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

`init/validate/package` 可在 Windows 执行，服务运行命令面向 Linux。固定镜像 digest；包版本不覆盖；快照回滚恢复版本和配置，数据库或hook外部副作用需独立恢复。默认仅监听 `127.0.0.1`，外部访问通过服务器网关或显式配置的绑定地址。
