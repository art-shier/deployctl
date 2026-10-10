# 运行时配置与安装钩子（ctl / 平台 >=1.5.0）

下文镜像、Compose、--release与Docker构建/运行参数适用于Docker项目；静态项目使用[静态部署](static-deployment.md)的模板、目录和统一命令。

`--env production` 继续选择部署环境。构建参数仍是 `build.args` / `build-args`，只交给 Dockerfile ARG；以下参数在服务器安装、升级时生效。

| 参数 | 用途 | 是否保留到下一次升级 |
|---|---|---|
| `--env-var KEY=value` | 应用配置，写入 JSON 并注入容器环境 | 成功后保留 |
| `--set KEY=value` | pre/post 安装参数 | 仅本次，两阶段共用 |
| `--unset-env KEY` | upgrade 删除持久化 override | 成功后生效 |

每个参数重复一次传一个键；按第一个 `=` 分割。值都是字符串，保留空值、空格、引号、`$`、`#`、更多 `=`。同组重名、裸键和 set/unset 同键拒绝。名称为大写 ASCII 标识符，最长128字符；每组最多128项、单值4096字符、总计64 KiB UTF-8，最终业务配置同样受限。拒绝控制字符、换行及 Unicode 行分隔符。`APP_VERSION` 和 `DEPLOYCTL_` 前缀是平台保留的应用配置名；安装参数使用独立前缀。

```bash
ctl install project-a --env production --release <真实发布包或URL> \
  --env-var LOG_LEVEL=info --env-var 'DISPLAY_NAME=团队 $ 服务' \
  --set DATA_DIR=/srv/project-a
ctl upgrade project-a --env production --release <真实发布包或URL> \
  --env-var LOG_LEVEL=debug
ctl upgrade project-a --env production --release <相同或新发布包> --unset-env LOG_LEVEL
```

有 `--set` 时，发布包必须声明至少一个 hook，避免安装参数被静默忽略。成功后的覆盖层顺序：`config.env → secrets.env → 上次成功的 env-var → 本次 unset/env-var`。unset只删除覆盖层，让服务器文件或镜像默认值重新生效；失败不会提交本次修改。原始服务器文件保持原样。restart沿用既有快照，要应用配置变更用upgrade；允许同版本重新部署，包内容仍不可变化。

## 应用读取 JSON

容器内文件固定 `/run/deployctl/.env.json`，只读 bind mount；路径由 `DEPLOYCTL_ENV_FILE` 告诉应用。本版只生成 JSON，值包括业务配置和平台 `APP_VERSION`；同一业务值也通过 Compose raw env_file 注入环境，安装参数不会进入应用文件或容器环境。

```python
import json, os
with open(os.environ['DEPLOYCTL_ENV_FILE'], encoding='utf-8') as file:
    config = json.load(file)
log_level = config.get('LOG_LEVEL', 'info')
```

项目自行在启动时读取，ctl不会修改业务启动命令。显式指定文件但读取失败应让启动失败；示例 `examples/project-a/app.py` 已演示。JSON文件权限644，位于服务器700目录内，使非root容器可读取文件挂载。业务账户也能读取所注入的全部配置；控制谁能执行docker exec。

服务器每次准备独立 `/etc/deployctl/<app>/<env>/runtime/<ID>/`，包含 `.env.json`、`effective.env`、`overrides.json`、`.install-params.json` 和平台生成的 `compose.yaml`。除JSON外文件为600，目录700。状态记录版本、快照ID、摘要和绑定，不记录参数值。已引用快照不得手工修改或删除。

## 项目可选 hook

在业务项目 `deploy/deployment.yaml` 增加，路径相对业务仓库根目录：

```yaml
hooks:
  pre_install:
    script: deploy/hooks/pre-install.sh
    timeout_seconds: 300
  post_install:
    script: deploy/hooks/post-install.sh
    timeout_seconds: 300
```

只配置需要的阶段。脚本必须是普通文件，无链接/路径穿越，最大256 KiB；超时1..3600秒，默认300。自包含Linux Bash脚本，无需执行位，使用LF行尾。流水线只打包，不执行hook。无hooks保持发布协议v1及四个原始文件；有hooks生成协议v2、固定脚本成员和SHA256，最低ctl1.5.0。`validate/package`均支持；项目需同步构建workflow调用引用与platform-ref至同一>=1.5.0平台引用。

脚本在**服务器宿主机**执行，身份和ctl相同，工作目录为该发布版本目录。运行环境仅系统基础环境、业务运行配置和以下上下文；不继承操作者GH_TOKEN/GITHUB_TOKEN。运行配置不能包含BASH_ENV、ENV、SHELLOPTS、BASHOPTS。脚本必须自带依赖，不能依赖包内未声明的其他文件。

| 变量 | 含义 |
|---|---|
| `DEPLOYCTL_PARAM_<KEY>` | 独立 `--set` 值，如 `DEPLOYCTL_PARAM_DATA_DIR` |
| `DEPLOYCTL_PARAMS_FILE` | 宿主机 `.install-params.json` 路径 |
| `DEPLOYCTL_ENV_FILE` | 宿主机 `.env.json` 路径，内容与应用相同 |
| `DEPLOYCTL_APPLICATION` / `DEPLOYCTL_ENVIRONMENT` | 应用/环境 |
| `DEPLOYCTL_IMAGE` | 已校验的候选镜像digest（>=1.6.0） |
| `DEPLOYCTL_VERSION` / `DEPLOYCTL_PREVIOUS_VERSION` | 候选/旧成功版本，首次安装旧版本为空 |
| `DEPLOYCTL_ACTION` | install 或 upgrade |
| `DEPLOYCTL_RELEASE_DIR` / `DEPLOYCTL_CONFIG_DIR` | 发布目录/服务器配置目录 |

```bash
#!/usr/bin/env bash
set -euo pipefail
data_dir=${DEPLOYCTL_PARAM_DATA_DIR:?supply --set DATA_DIR=...}
install -d -- "$data_dir"
```

引用变量，不用eval，也不source JSON或配置文件。hook有部署身份的主机权限，适用于可信团队代码。

## 顺序与恢复

准备/校验/拉镜像 → 写事务 → pre → 启动 → 就绪检查 → post → 再次就绪与快照验证 → 提交成功。post执行时容器可能已经接收流量；没有摘流量或零停机保证。pre失败不替换容器；启动/健康/post失败恢复旧版本**及旧快照和绑定**，首次安装失败停止候选，仍退出非零。超时或中断清理整个脚本进程组；中断、恢复失败保留pending，修复后用rollback。rollback/restart/stop/恢复均不重跑hook。

同版本配置变化也会产生previous，rollback能恢复配置；完全相同的运行值、override层和绑定重试保留原previous。数据库、文件系统、对象存储等hook外部副作用不会撤销，脚本应幂等且兼容新旧版本。

旧状态status只读。首次从旧版升级会校验实际容器身份并捕获真实配置作为回滚基线，不用已编辑的服务器文件冒充历史值；容器缺失/身份不符时拒绝迁移。旧历史版本尚未捕获的引用仍使用原服务器文件行为，不能承诺其配置回滚。

hook日志在受保护 `hook-logs/`，权限600，只保留末尾64 KiB；错误仅显示阶段/退出码/日志位置，不自动回显任意输出。应用失败日志可能含业务信息，同样按秘密文件保管。

## GitHub SSH 部署参数

在可选deploy调用中增加文字输入，项目自行填充；敏感值从受控secrets/配置管理取得，不填写到仓库或workflow_dispatch公开输入：

```yaml
jobs:
  deploy:
    uses: art-shier/deployctl/.github/workflows/deploy.yml@v1.5.0
    with:
      platform-repository: art-shier/deployctl
      platform-ref: v1.5.0
      # 原有 application/environment/release-url/sha256/mode 等照旧
      runtime-env: |
        LOG_LEVEL=info
      install-params: |
        DATA_DIR=/srv/project-a
```

每行一个原始KEY=value，空行忽略，值不加shell引号。Runner校验后写600 JSON，与校验过的包一并SCP；服务器std­lib helper重新校验，以argv执行ctl，值不进入SSH shell文本。带参数时要求服务器ctl>=1.5.0。未提供参数的调用保持原流程；远程Python>=3.10，无需pip。这个workflow不提供unset输入，需要删除override时用CLI。

## pre-install 生成配置（>=1.6.0，待发布）

默认hook顺序不变。需要pre生成配置时显式声明：

```yaml
hooks:
  pre_install:
    script: deploy/hooks/pre-install.sh
    refresh_config: true
```

该字段仅允许pre且必须为布尔值，发布包最低ctl1.6.0；旧CLI会拒绝，不能用v1.5.0运行。pre前仍验证配置格式、控制字符、路径和权限，但必需值检查延迟到pre成功之后。hook写普通服务器config.env/secrets.env，不能修改任何runtime快照。

pre成功后ctl重新读取服务器文件，按原有override/unset顺序合并，校验必需值与Bash控制变量，再创建新的不可变快照用于启动和post。初始快照保留不改。无变化时沿用初始快照。失败保留旧容器/成功快照；rollback不重跑hook，也不撤销hook对普通服务器文件或数据库的副作用。
