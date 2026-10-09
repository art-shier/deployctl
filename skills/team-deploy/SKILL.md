---
name: team-deploy
description: "Use when onboarding a repository to team-deploy's GitHub Actions and Docker Compose delivery, installing or updating its ctl/deployctl CLI, or operating its managed services. 适用于项目接入、ctl命令缺失或工具升级、标准发布包和服务运维；不用于通用 K8s 或其他平台部署。"
---

# Team Deploy

使用已有 Team Deploy 工具和契约完成工作。随附CLI为v1.11.0，兼容发布包协议v1/v2并支持管理服务自动Release安装及快捷运维命令、项目/项目组管理、发布凭据配置读写、项目组配置继承、可编辑凭据与真实镜像层下载进度；skill是agent的操作指南，实际构建和部署由流水线及deployctl执行。

## 选择任务

- 读写项目/组配置或管理项目：读取 [管理服务模式](references/control-plane.md)。CLI>=1.11.0支持project/group及project-config/group-config；发布凭据只管理授权项目，组管理和删除需要owner。
- 安装或升级ctl服务端：读取 [管理服务模式](references/control-plane.md)。CLI>=1.10.0支持server-install/server-upgrade默认获取官方最新正式Release，或指定--version/--release；旧server install/upgrade写法兼容。服务端首次安装不需要平台登录，业务安装仍需要scoped Token。

- 注册到ctl管理服务、托管镜像/配置、login/publish或无`--release`安装：读取 [管理服务模式](references/control-plane.md)，先确认目标CLI>=1.7.0和真实服务地址。

- 项目接入或生成发布包：读取 [项目接入](references/onboarding.md)，新接入优先使用deployctl init，已有配置按需合并。
- 项目构建脚本需要参数、配置build.args或工作流覆盖：读取 [构建参数](references/build-args.md)。init保持基础配置，项目后续自行填写。
- install/upgrade需要应用JSON配置、env-var/set/unset或pre/post安装脚本：读取 [运行时配置与钩子](references/runtime-config-hooks.md)，先核对CLI>=1.5.0和两个workflow引用。
- ctl/deployctl找不到、PATH问题、首次安装或工具自身升级：读取 [CLI安装与升级](references/cli-lifecycle.md)。
- 服务器安装、升级、回滚或排障：读取 [服务器操作](references/operations.md)。
- 其他部署体系保持其原有方式，不把 K8s、云函数或多组件系统强行改为本模板。

先从用户说明和现有仓库确定项目目录、application、平台仓库及引用；服务器操作还需要目标主机、环境、版本和配置目录。缺少的关键参数应明确提出，同时继续不依赖它的本地准备。示例仓库名不是实际部署目标。

## 工具定位

将当前 `SKILL.md` 所在目录记为 `SKILL_DIR`。选择可执行的 Python >=3.10 解释器，运行随附的自包含 CLI，无需 pip：

先检查目标环境中的 `ctl` 或 `deployctl` 以及 `--version`。命令缺失时按 [CLI安装与升级](references/cli-lifecycle.md) 区分PATH问题与未安装；仅本地init/validate/package可以直接使用下面的随附CLI，不必为此安装系统命令。

```bash
python "$SKILL_DIR/assets/deployctl.pyz" --version
python "$SKILL_DIR/assets/deployctl.pyz" init --help
python "$SKILL_DIR/assets/deployctl.pyz" validate deploy/deployment.yaml
```

本地init/validate/package可在Windows执行；服务运行命令只能在目标Linux服务器执行。服务器已安装CLI时先检查版本和--help；版本/契约不一致时查对应说明。v1.0.0没有init，使用随附v1.5.0初始化；远程平台引用沿用用户指定的真实版本。

`ctl self-update` 更新部署工具本身（CLI>=1.3.0且由安装器管理），`ctl upgrade <application> --env ... --release ...` 更新业务服务。旧CLI、源码/直接运行pyz或缺少安装记录时，通过安装器准备受管理的命令，不猜测其支持self-update。

## 交付契约

| 文件/产物 | 来源 |
|---|---|
| `Dockerfile` | 项目已有文件，必要时按原技术栈补齐 |
| `deploy/deployment.yaml` | 项目填写，必须通过 CLI validate |
| `.github/workflows/release.yml` | 调用公共 build-release.yml；输入以随附模板为准 |
| `release.yaml`、`compose.yaml`、`.env.example`、`README.md` | 流水线生成并放入 tar.gz |
| 可选 `hooks/pre-install.sh` / `hooks/post-install.sh` | 项目声明后校验、打包；需要协议v2/ctl>=1.5.0 |
| tar.gz 外部 `.sha256`、镜像 digest | 真实构建输出，不虚构实际产物 |

保留现有 Dockerfile、测试和工作流；按需编辑或新增，不直接覆盖。工作流调用的 `@引用` 和 `platform-ref` 必须相同，`platform-repository` 与调用仓库匹配，`version` 使用项目标签。生产推荐固定同一审核过的 SHA。

项目接入请求授权本地修改，不自动代表授权推送标签、发布 GitHub Release 或操作生产服务器。用户已明确授权具体发布/部署时直接完成该范围内的步骤，不反复确认。

## 运行约束

- 一个发布包对应一个无状态 HTTP 服务，单机替换可能短暂中断；不承诺多机编排或零停机。
- 固定镜像 digest，校验发布包 SHA256；不能为绕过失败改用 `latest`、手工改 Compose 或删除 state.json。
- 密钥留在服务器配置/凭证管理中，不进入 Git、发布包、命令 URL 或对话输出。raw env_file 保留 `$`；不要加 shell 引号。
- 快照部署回滚恢复版本、配置和绑定；未捕获的旧历史引用仍用服务器文件。数据库/文件系统等hook副作用需另行恢复。
- 退出码非零就是发布失败，即使旧版本恢复成功。pending transaction 先诊断再恢复，不能继续 upgrade。

## 完成时报告

报告修改文件、application/平台引用或目标环境、实际执行的校验及退出结果。服务器操作附实际当前版本和事务状态。区分本地配置通过、CI 构建通过和服务器就绪；没有运行的 Docker、GitHub 或 SSH 检查要明确说明。

pre需要生成配置时用 `refresh_config: true`，要求新CLI>=1.6.0；先核对真实已发布版本，不将源码版本视为已发布。
