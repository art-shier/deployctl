# 部署参数、容器配置文件与安装脚本设计

日期：2026-10-07。基线：正式CLI/平台v1.4.0。本文是待审阅的设计，所述新功能尚未实现；计划作为v1.5.0交付。

## 用户目标与范围

用户希望在install时传入KEY=value参数，由ctl生成配置文件，让Docker内的项目在启动时读取；可选pre-install、post-install脚本也获得相同运行参数，并可接收独立的安装参数。现有--env已经表示部署环境，用户允许换一个参数名，同时要求--set保留安装/脚本参数的含义。

本方案采用可重复的--env-var传入应用运行配置、--set传入安装脚本参数，保留--env及全部既有命令用法。默认生成.env.json，第一版只提供JSON格式，所有配置值都是字符串。它能准确保存空值、空格、等号、引号、美元符号和井号，项目无需依赖不同dotenv解析器的行为。后续确有需求时再增加dotenv输出格式。

沿用项目config.env、secrets.env，不增加全局公共env层，不把运行参数写进源码或Release资产，不改变CI的build-args语义。init仍生成基础接入文件，项目按需自行声明hooks。

脚本执行位置采用目标Linux服务器，以调用ctl的身份运行；临时容器执行模式不在本版范围内。它适合检查依赖、准备目录和部署验证。脚本执行位置可在本设计审阅时调整。

## CLI与参数契约

```bash
ctl install project-a --env production \
  --release <发布包路径或地址> \
  --env-var LOG_LEVEL=info \
  --env-var FEATURE_ENABLED=true \
  --env-var 'DISPLAY_NAME=Project A' \
  --set DATA_DIR=/data/project-a

ctl upgrade project-a --env production \
  --release <下一版本发布包路径或地址> \
  --env-var LOG_LEVEL=debug \
  --set DATA_DIR=/data/project-a
```

- install和upgrade增加可重复的--env-var KEY=value和--set KEY=value，每次出现只接收一个键值；两类参数独立解析。
- 在第一个等号处分割；KEY=明确表示空字符串。裸KEY、重复键及非法名称拒绝。
- 键沿用大写ASCII环境变量名规则，最多128字符；每类参数最多128项，每个值最多4096字符，每类总输入不超过64 KiB UTF-8。最终合并的业务配置也限制为128个键、每个值4096字符及64 KiB UTF-8；平台自动生成值单独计算。
- 值保留首尾空格和特殊字符；拒绝控制字符、换行及Unicode行分隔符。不做shell求值或数字/布尔类型推断。
- APP_VERSION和DEPLOYCTL_前缀由平台管理，不允许业务配置或--env-var覆盖。
- 成功的--env-var覆盖项独立持久化；下次upgrade未重复传入时仍保留。增加可重复的--unset-env KEY以删除持久化覆盖项，让该键重新使用服务器配置文件的值；同一键同时env-var/unset-env拒绝。
- install不接受--unset-env；upgrade失败时不提交候选覆盖项。
- --set只供本次pre/post脚本执行使用，不注入应用容器或.env.json，下次upgrade需要重新传入。传入--set但发布包没有任何hooks时拒绝，避免静默忽略参数。
- 同名运行参数与安装参数可以并存，脚本侧通过不同入口访问，不相互覆盖。
- 命令行参数用于普通配置；秘密仍可通过既有secrets.env提供，不需要通过命令行传入。

运行配置来源从低到高为：config.env → secrets.env → 当前成功部署的持久化覆盖项 → 本次--unset-env删除覆盖项及--env-var变更。最后注入平台保留值APP_VERSION。required_config校验针对最终合并结果执行，因此--env-var也能满足必填字段；--set不参与应用必填配置校验。

## 配置文件与容器读取

每次部署生成独立配置快照，目录位于：

```text
/etc/deployctl/<application>/<environment>/runtime/<configuration-id>/
├── .env.json          # 交给项目读取的最终配置
├── effective.env      # 同一份配置的Compose raw env_file表示
├── overrides.json     # 持久化的CLI覆盖层
├── .install-params.json # 本次脚本参数，服务器使用，不挂载到应用容器
└── compose.yaml       # ctl从已校验契约生成的实际运行配置
```

configuration-id由ctl生成，不使用用户参数拼接路径。目录权限700；effective.env、overrides.json、.install-params.json及运行描述权限600。.env.json为644，但放在权限700的目录内，以便只绑定该文件时，使用非root用户的容器也能读取。实际config.env、secrets.env保持原文件及权限，不被命令修改。

例如用户参数生成如下文件；APP_VERSION由ctl加入：

```json
{
  "LOG_LEVEL": "info",
  "FEATURE_ENABLED": "true",
  "DISPLAY_NAME": "Project A",
  "APP_VERSION": "v2.0.0"
}
```

ctl将该文件只读绑定到容器固定路径/run/deployctl/.env.json，并注入DEPLOYCTL_ENV_FILE=/run/deployctl/.env.json。业务项目在启动时读取这个变量指定的文件。ctl不假定应用工作目录，也不覆盖应用镜像中的/app/.env。

同一份最终业务配置同时用于容器环境变量和JSON文件。容器镜像本身的ENV仍可提供未显式配置的变量；JSON只包含ctl管理的业务配置及APP_VERSION。

实际运行Compose由ctl基于已经验证的标准模板生成，只增加受控配置文件挂载、快照env_file及平台上下文。Release内原compose.yaml保持不变并继续严格校验；不能借此接受用户任意Compose扩展。配置快照标识加入容器label，健康验证同时确认镜像、版本和配置标识，以验证同版本配置更新真正生效。

## 可选安装脚本

业务项目在deployment.yaml中自行增加：

```yaml
hooks:
  pre_install:
    script: deploy/hooks/pre-install.sh
    timeout_seconds: 300
  post_install:
    script: deploy/hooks/post-install.sh
    timeout_seconds: 300
```

两个阶段均可省略；配置了阶段必须提供存在的普通脚本文件。script相对业务项目根目录，不允许绝对路径、父级跳转、符号链接或项目目录外的目标。每个脚本最多256 KiB；timeout_seconds为1..3600的整数，默认300。

流水线只打包脚本，不执行脚本。服务器使用Bash执行，工作目录为候选发布目录；脚本应自包含，平台不自动打包项目源码、辅助脚本或依赖。调用使用参数数组，不把运行参数拼入shell命令字符串。

pre/post获得与容器相同的业务运行参数；--set安装参数独立写入.install-params.json，并以DEPLOYCTL_PARAM_<KEY>形式提供给脚本。例如--set DATA_DIR=/data/project-a对应DEPLOYCTL_PARAM_DATA_DIR。脚本还获得以下只读上下文：

| 名称 | 值 |
|---|---|
| DEPLOYCTL_APPLICATION | 项目名称 |
| DEPLOYCTL_ENVIRONMENT | --env指定的部署环境 |
| DEPLOYCTL_VERSION | 候选业务版本 |
| DEPLOYCTL_PREVIOUS_VERSION | 部署前的成功版本；首次安装为空 |
| DEPLOYCTL_ACTION | install或upgrade |
| DEPLOYCTL_RELEASE_DIR | 服务器上的候选发布目录 |
| DEPLOYCTL_CONFIG_DIR | 项目服务器配置目录 |
| DEPLOYCTL_ENV_FILE | 服务器上候选.env.json的绝对路径 |
| DEPLOYCTL_PARAMS_FILE | 服务器上本次.install-params.json的绝对路径 |

服务器脚本与容器的DEPLOYCTL_ENV_FILE分别指向各自环境中可读取的同一份运行配置。安装参数通过DEPLOYCTL_PARAMS_FILE或DEPLOYCTL_PARAM_<KEY>读取。脚本内export不改变ctl后续配置，修改配置文件也不作为受支持的输出接口。平台验证快照没有被修改后才继续部署。

hook子进程不继承下载凭证等无关父进程变量，仅提供基础系统执行环境、业务配置及上述上下文。启用主机hooks时，业务配置中的BASH_ENV、ENV、SHELLOPTS、BASHOPTS拒绝，以避免解释器自动加载额外代码；父进程的这些变量也不继承。子进程管理覆盖超时和中断时的进程组清理。

## 发布包与版本兼容

没有hooks的项目继续生成原schema_version: 1、minimum_deployctl_version: 1.0.0和四个标准文件；新版ctl可以为既有v1发布包生成运行配置文件，无需重发业务包。

含hooks的包使用schema_version: 2、minimum_deployctl_version: 1.5.0。项目源hooks配置转成发布描述中的明确清单，包含固定路径、SHA256和超时；包内只增加声明过的hooks/pre-install.sh及hooks/post-install.sh。服务端同时检查外部归档摘要、脚本摘要和精确成员集合，并保留原有解压预算、普通文件、重名、路径和Compose校验。

新版读取器支持v1/v2；旧版拒绝v2，不能静默跳过脚本。启用hooks的项目需更新调用workflow及platform-ref到同一新版本，并升级服务器ctl。

## 部署事务与失败行为

```text
校验包、准备候选配置、校验必填项
  → 检查Docker及Bash、拉取镜像
  → 持久化部署事务
  → pre-install
  → 启动候选容器
  → 验证镜像、版本、配置标识及健康
  → post-install
  → 再次验证候选容器和健康
  → 原子提交成功部署及配置快照
```

- 首次install和upgrade均执行候选包声明的hooks，脚本通过DEPLOYCTL_ACTION区分动作。
- 准备、下载、配置、依赖检查、拉取或pre失败，旧服务不被替换。pre外部副作用不自动撤销。
- 启动、健康检查或post失败，恢复部署前的版本及配置快照并验证；本次命令仍返回非零。首次安装失败停止候选容器。
- 恢复失败保留pending事务，阻止继续部署。事务记录候选/原部署引用、配置引用和阶段。
- 中断时停止正在执行的hook进程组并保留pending；rollback负责恢复，不自动重跑hook。
- 正常rollback恢复上一次成功部署及其配置；stop、restart、自动恢复和手动rollback均不执行pre/post。
- 同版本upgrade也可更新参数。只有相同版本且参数未变化的重试才保留原previous；成功的同版本配置变更可回滚到该版本的上一份配置。
- 脚本修改的数据库、文件和外部系统不纳入自动恢复。脚本需要能重复执行；post之前候选容器可能已经接收流量，本功能不提供流量切换保证。

新的运行状态使用schema_version: 2，current/previous为包含业务版本和配置快照标识的部署引用。对旧v1状态提供读取与迁移；只读诊断不触发迁移。升级旧安装前，从已有容器取得实际业务运行配置，建立可恢复基线；缺少容器时明确拒绝该升级并要求先恢复既有服务。没有容器实例可取配置的旧历史版本按原v1配置行为恢复，并在状态中标明legacy配置来源。

快照只在本地保存，不进入Release。部署状态、current指针和events仅记录标识，不输出参数值。hook输出写入权限600、大小有上限的日志；CLI失败报告阶段、退出码/超时及日志路径，不自动回显脚本任意输出。

## 接入、工作流与验收

项目启动代码读取DEPLOYCTL_ENV_FILE指向的JSON，接受字符串值并按业务需要转换类型。无需重新init；声明hooks时按需增加YAML和脚本。

SSH reusable workflow增加可选runtime-env和install-params输入，分别使用KEY=value行表示运行配置和安装参数；在Runner验证后作为数据文件传送到服务器，由平台脚本分别解析为--env-var与--set参数数组。不要将值插入SSH命令字符串，不写入Release。服务器仍从secrets.env读取秘密配置。

交付包含CLI、包协议和Schema、reusable workflows、模板注释、接入/运维文档、team-deploy skill和版本化发行资产。

验收必须覆盖：

1. 保留原--env参数；--env-var/--unset-env/--set解析、空值、首尾空格、特殊字符、重复键及保留键校验。
2. 必填配置可由--env-var满足；后续升级继承运行配置覆盖层，unset-env恢复服务器配置值；安装参数独立且不进入应用配置。
3. JSON与容器环境变量、主机hook读取值一致；非root容器可读且文件挂载只读。
4. 正确脚本执行顺序；pre失败不替换旧容器，post失败恢复旧版本及旧配置。
5. 超时、中断、子进程组清理、恢复失败及pending阻断。
6. 配置快照持久化、同版本配置变更回滚、旧安装迁移和legacy回滚。
7. 未声明脚本、摘要错误、符号链接、路径穿越、重名和超限包均在执行前拒绝。
8. Linux真实Docker测试实际读取挂载JSON，并完成首次安装、升级、失败恢复和回滚；Bash hook也实际执行，不只使用测试替身。
9. Linux/Windows适用单元测试、actionlint、Schema、自包含CLI、安装/自更新、发行校验和与skill验证。

测试和发布在平台仓库完成；没有目标服务器授权与连接信息时，不执行生产服务操作。
