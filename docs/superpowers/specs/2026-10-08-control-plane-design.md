# ctl 服务端与管理台技术设计

日期：2026-10-08。用户已确认功能方案，本文固定实现边界与接口，供开发前审阅。现有公开 CLI 为 v1.5.0；本地功能基线为已通过 CI 的 pre-install 配置回读提交 `9e62828e4f2a31f1b06d9bb5a500a16baafb8609`，尚未合并或发布。以下是新增能力，不表示当前 CLI 已支持。

## 目标与组件

项目接入一次、目标服务器登录一次，之后通过 `ctl install notes --prod` 下载受检版本、获取管理台配置、生成本地运行快照并启动。保留本地文件和 HTTPS 发布包安装方式。

ctl 服务端管理项目、版本、环境配置、权限及安装结果；OCI Distribution Registry 保存镜像；发布包保存在独立制品目录；PostgreSQL 保存元数据和加密配置；React/TypeScript 管理台操作 API。CLI 继续在目标 Linux 主机运行 Docker Compose 和可信 pre/post 钩子。

首版是单组织自托管平台。网页直接远程部署、常驻服务器 Agent、多机编排、自动证书管理、扫描/签名系统和 SSO 不属于首版。管理台展示 CLI 上报结果，而不是将其当作实时主机监控。

采用 Go HTTP 服务、PostgreSQL、React/TypeScript/Vite。现有 Python>=3.10 CLI、标准发布包 v1/v2、Compose>=2.30 和 raw env_file 保留。服务端用独立 Compose 引导部署，因为当前业务发布契约只描述一个 HTTP 服务。

## 项目与环境模型

- Project：不可变 slug（沿用现有 application 校验）、可修改名称/说明、代码仓库、镜像来源、允许的镜像仓库路径、默认环境。slug 也作为 OCI repository；Notes 为 `notes`。
- Release：project、唯一 version、commit、image digest、package SHA256/size、标准包契约、状态和创建人。状态为 draft/published/retired；published 内容不可覆盖。
- Environment：project+name 唯一，name 沿用现有环境标识规则。保存当前配置修订与目标版本选择（固定版本或 stable 通道）。注册项目时自动创建其默认环境、空配置修订1和stable目标；Notes 的默认环境为 prod。
- ConfigurationRevision：不可变 ID、递增 revision、runtime_env、install_params、deployment_defaults、创建人/时间。所有配置值加密存储，秘密标记控制界面展示。
- Token：仅存哈希，role 为 publisher/deployer，绑定项目，deployer 另绑定允许的环境。支持失效时间和撤销，创建时仅返回一次明文。
- DeploymentReceipt：host_id、project、environment、release、configuration_revision、成功/失败、时间、CLI 版本；不包含配置值或原始日志。
- AuditEvent：操作者、动作、项目/环境、配置键名或版本、时间；不存参数值、Token、密码及邀请 URL。

owner 管理项目、环境、Token 和版本；publisher 只发布指定项目，不读取生产配置；deployer 读取授权环境的配置和镜像。管理台首版使用 owner 登录。应用项目的最终业务验证仍由项目负责。

环境名与版本选择互相独立：prod 不等于 latest。环境可以指向固定版本，或 stable 指针；CI通过publish的显式 `--channel stable` 在完整发布成功后原子更新stable，未提供时不更新。初次接入生成的流水线使用该参数，直接install能找到目标；指针未指向可用版本时返回明确错误。目标已停止使用的版本标记 retired，禁止新安装；历史快照与回滚需要的镜像/包保留。首版不自动清理制品。

## 镜像与标准包发布

推荐托管 Distribution Registry，业务流水线使用标准 docker/build-push-action 推送多架构镜像。服务端管理目录与权限，不自行实现镜像分层上传协议。

流水线在测试通过后推送镜像，取得 manifest/index 的真实 sha256 digest，再使用当前 packager 生成标准包。镜像传完尚不代表版本已发布。流水线调用发布 API 上传标准包和元数据，服务端完成以下检查后原子提交 published：

1. Token 对应项目一致，版本未发布；同版本同内容可幂等重试，内容不同返回409。
2. 实际包 SHA256、有限大小、安全 tar 成员及 release.yaml 元数据匹配；成员/钩子校验沿用现有契约。
3. 镜像必须固定 digest，repository 匹配项目允许路径，Registry 能提供指定 manifest/index。
4. 包先进入临时文件，校验完成后持久化；元数据发布失败不能产生可安装的残缺版本。失败临时文件可清理，孤立已上传镜像可在管理台显示。

镜像来源可以是托管 Registry 或登记的外部仓库，例如 GHCR。首版外部私有仓库采用目标主机已有 Docker 凭据；平台不集中转发任意外部凭据。外部版本依然登记完整标准包和 digest。

变更镜像地址或 digest 创建新的发布记录并保留项目部署描述；不能修改旧发布包。首版兼容方式是 `--release` 或登记外部镜像版本；直接 `--image` 替换受检包镜像不在首版中，以免破坏包校验和不可变版本。

## 配置字段与合并规则

runtime_env 对应现有 `--env-var`；install_params 对应现有 `--set`；deployment_defaults 只允许 host_port、bind_address、memory_limit、cpus。构建参数不属于运行配置；Docker CMD/任意 Compose 内容不允许通过配置表注入。

值保持字符串。每组最多128项、键最长128字符、值最长4096字符、总 UTF-8 不超过64 KiB；最终运行/安装参数仍执行相同限制。区分不存在与空字符串。拒绝控制字符、重复键、平台保留 runtime 键和 Bash 启动控制变量。不求值、不 source 配置、不展开 `$` 或 `${...}`。

运行配置从低到高为config.env、secrets.env、上次成功的本地 env-var 覆盖、本次 unset/env-var、管理台选定修订的 runtime_env。项目的运行默认值来自镜像ENV；镜像 ENV 是容器的最终后备默认，ctl 不虚构未显式配置的镜像值，不新增另一套全局参数表。APP_VERSION 等平台字段在最终校验后注入。

安装参数从低到高为本次 --set、管理台该修订的 install_params；项目脚本自己的后备默认保留。本次命令行安装参数不作为下一次升级的本地覆盖层；管理台安装参数随修订保存。配置了安装参数但发布包没有钩子时拒绝，延续当前行为。

端口/绑定/资源属于部署参数：项目描述默认值 → 管理台默认值 → 显式命令行值。host_port 和 bind_address 在 install/upgrade 可按主机覆盖；没有显式修改时保留当前主机绑定。资源变化只有在显式安装/升级时应用。既有服务不会因管理台保存自动重启。

管理台删除键后，下一次 upgrade 回退到较低配置层；显式空值覆盖低层，是否允许空值由项目验证。CLI 的 unset 只移除本地覆盖，不能移除管理台最高层键。管理台显示最终来源及被覆盖的键名，不显示秘密值。

管理台保存配置创建新修订，并要求基于前一修订的乐观锁，避免多人保存丢失更新。读取时变量是 `{key,secret,configured,value?}` 列表，秘密没有value，普通字段返回实际value。修改请求带expected_revision；runtime_env/install_params各为 `{key,operation,secret?,value?}` 列表，operation为keep/set/remove；set必须带字符串value，keep/remove禁止带value，不能把展示占位符误当作密码。整份配置使用 AES-256-GCM，随机 nonce，AAD 绑定 project/environment/revision。加密主密钥与数据库备份分别保管。

## 原子解析与安装事务

`POST /api/v1/projects/{slug}/resolve` 使用同一数据库事务选定发布版本和配置修订，返回固定 deployment resolution。命令执行过程中不会再次取 latest 或当前配置。返回类型见下文。

1. CLI 检查服务端地址、授权、发布资料、配置格式和限制，再下载/校验包。网络、权限、格式错误在替换服务前失败。
2. 合并本地层与管理台层，生成初始只读快照；合并安装参数，将其交给钩子。
3. pre 按项目契约执行。启用 refresh_config 时回读普通 config.env/secrets.env。
4. 回读后的本地层重新叠加本次相同修订的管理台层，校验必需值和限制，再生成独立不可变快照。pre 不能覆盖管理台最高层，也不能改写快照。
5. 启动、就绪、post、再次就绪后提交当前状态。失败恢复上次成功版本、绑定和实际运行快照；数据库/源文件/邀请副作用不回滚。
6. best-effort 上报安装结果，上报失败不把已成功部署改成失败，也不触发回滚；客户端记录待补报的非秘密结果。

本地 snapshot 保存最终值和安装参数，local overrides 独立保存，不能将远端值写入 local overrides。快照新增可选 `.management.json`（平台 origin、project、environment、release_id、revision_id及生效的资源覆盖，无秘密值），并纳入完整性校验；读取器支持没有该文件的旧快照。资源覆盖只允许memory_limit/cpus，Compose由发布描述加受检覆盖重新生成并验证，旧发布包不改写；回滚从快照复原真实资源配置。新快照最低由新增平台 CLI 读取，不宣称旧CLI支持。

rollback/restart/stop 使用上次本地状态，不读取最新管理台配置、不重跑钩子。本地已缓存且验证过的镜像/包可以回滚；缺少镜像且远端不可达时明确失败，保留事务供恢复，不承诺任意离线回滚。

## CLI 接口与旧模式

拟新增命令：

```bash
sudo ctl login --server https://ctl.shier.art
sudo ctl install notes
sudo ctl install notes --prod
sudo ctl install notes --env test --version v0.3.0
sudo ctl upgrade notes --prod --port 9000
ctl publish notes --version v0.3.0 --package dist/notes-v0.3.0.tar.gz --channel stable
```

这些域名和版本是计划示例，未配置 DNS、未发布新版本。login 通过交互或私有 token 文件读取凭据，不能从命令 URL 读取 Token。Linux 凭据配置为 `/etc/deployctl/client.json`（目录700、文件600、当前身份所有，拒绝链接），支持显式配置路径；业务容器不会获得平台 Token。

`--prod` 是 `--env prod` 的别名，二者冲突拒绝。平台模式未指定环境时使用项目默认；首次 install 未指定 version 时使用该环境目标，upgrade 同理。status/rollback/restart 等未指定环境时使用当前主机唯一已安装环境；存在多个时要求明确选择，不能靠访问远端决定本地操作对象。

明确 --release 默认走旧模式：--env 保持必填，旧下载/私有GitHub/本地文件/校验行为保持，无需平台登录。--with-platform-config 可以选择叠加平台配置；--release 与 --version 冲突拒绝。安装参数只使用 --set；环境只使用 --env/--prod，不复用字段。

服务端接受 HTTPS；本地回归只允许 localhost/127.0.0.1 测试使用 HTTP。Token 不发送到任意重定向或未授权域名。平台制品下载 URL 为同源 API 路径。Registry 凭据是短时、指定 repository 权限的凭据，CLI 使用临时私有 DOCKER_CONFIG，不将其注入应用、快照或钩子。

## API 契约

API 为 `/api/v1`。owner 管理台采用 HttpOnly/SameSite 会话 Cookie，同源 JSON 写入并校验 Origin；CLI/CI 用 Bearer Token。错误 JSON 为 `{code,message}`，不包含配置值、请求认证头或存储错误详情。

| 接口 | 权限 | 行为 |
|---|---|---|
| POST /session；DELETE /session | owner；当前会话 | 登录/退出，Cookie与权限检查 |
| GET/POST /projects；PATCH /projects/{slug} | owner或授权项目读取 | 注册/编辑元数据，slug不可变 |
| GET /projects/{slug}/releases | 授权项目 | 列表，不返回生产配置 |
| POST /projects/{slug}/releases | publisher或owner | multipart上传包，验证并原子发布 |
| POST /projects/{slug}/releases/{version}/retire | owner | 停止新安装，保留原内容 |
| GET/PUT /projects/{slug}/environments/{name} | owner | 读取脱敏配置/目标版本；带expected_revision保存 |
| POST /projects/{slug}/resolve | deployer或owner | env/version可选，一次返回版本+配置修订 |
| GET /projects/{slug}/artifacts/{release_id} | 授权项目拉取 | 已验证标准包，同源下载 |
| POST /projects/{slug}/receipts | deployer | 保存非秘密安装结果，host_id限定 |
| GET /projects/{slug}/receipts；GET /audit | owner | 部署结果和变更记录 |
| POST/DELETE /tokens | owner | 最小权限Token创建/撤销 |
| GET /registry/token | Registry登录身份 | 签发短时OCI认证Token，不授予未授权repository/actions |

resolve 响应固定为：

```json
{
  "schema_version": 1,
  "minimum_client_version": "1.7.0",
  "project": "notes",
  "environment": "prod",
  "release": {
    "id": "immutable-release-id",
    "version": "v0.3.0",
    "image": "registry.shier.art/notes@sha256:<actual-digest>",
    "package_path": "/api/v1/projects/notes/artifacts/immutable-release-id",
    "sha256": "<actual-package-checksum>"
  },
  "configuration": {
    "id": "immutable-revision-id",
    "revision": 3,
    "runtime_env": {},
    "install_params": {},
    "deployment_defaults": {}
  }
}
```

CLI再校验项目/环境/版本、同源路径、digest、checksum和配置值。引用字段示例不是可用产物。CLI1.7.0与服务端0.1.0为拟交付版本；旧包的minimum_deployctl_version及既有协议不修改。

Registry使用标准Bearer Token认证，ctl服务端签发RS256短时JWT，scope严格绑定repository与pull/push。签名私钥由服务端读取，Registry只持有验证证书；实际Registry集成测试必须证明跨项目和越权push被拒。

## 管理台交互

登录后首页为项目列表。项目详情页包含版本、环境、安装记录和访问凭据四个页签，审计单独展示。版本详情显示来源、digest、checksum、commit、状态及安装命令。环境页分别编辑业务变量、安装参数和部署默认值，并选择目标版本。

变量支持新增、编辑、删除、秘密标记、显示配置来源；秘密保持时不发送占位符。保存前展示键名差异，成功后显示新修订。发生409时保留当前草稿，要求重新加载对比；不能静默覆盖他人修改。提供空列表、加载、鉴权失败、网络失败和表单校验状态。

Token仅创建时显示一次，后续只显示标识、权限、期限和撤销操作。安装记录显示客户端上报时间，不能以历史成功伪报当前在线。

## Notes 接入与交付

Notes保留现有Dockerfile和部署描述。流水线增加推送托管仓库与ctl publish；可继续发布GitHub Release，旧入口继续可用。publisher不读取notes/prod配置。

Notes pre优先使用已合并的有效DATABASE_URL并执行只读数据库检查。存在该值时不强制ConfigHub CLI/Token、不再次拉取ConfigHub；缺少时保留原ConfigHub生成流程。生产缺失连接仍禁止SQLite后备。post使用最终快照，empty状态才按ADMIN_EMAIL生成邀请。图片/导出持久化按此前决定继续暂缓。

平台Compose包括API/管理台、Registry和持久制品目录，PostgreSQL可独立部署或接现有服务的专用deployctl库。TLS反向代理指向管理/API和Registry两个入口；ctl.shier.art/registry.shier.art仅为建议域名。引导部署生成独立加密密钥、JWT签名密钥、首个owner凭据，秘密只写私有文件。数据库、制品、Registry卷和加密密钥分别备份，不能仅备份数据库。

交付按服务端、CLI、管理台和Notes接入四个阶段实现。验收使用随机临时PostgreSQL、真实Registry和真实Docker服务，不读取生产ConfigHub/数据库，不修改已有Release。版本发布、生产部署仍采用具体授权范围。

## 验收条件

1. 注册Notes后流水线能推送镜像和标准包；仅完整验证过的版本可被安装，多架构digest保持。
2. 空服务器登录后通过ctl install notes --prod完成真实安装，操作员无需复制Release URL/checksum。
3. 管理台runtime和install参数覆盖本次CLI同名值；pre生成文件后仍保持此优先级。
4. 管理台删除键后回退到本地层；秘密占位符不会成为真实值，空值不会被当成不存在。
5. 保存配置不重启服务；upgrade固定一次发布/修订；安装中途修改管理台不会改变候选配置。
6. pre、配置校验、启动、健康、post失败保持或恢复旧服务与真实旧快照；rollback不读新配置。
7. publisher不能读取生产配置，prod deployer不能读test或其他项目，Registry不能越权push/pull。
8. 登录退出、撤销Token、配置冲突、空状态/网络错误有明确反馈；旧--release方式无需服务端。
9. 日志/制品/安装结果不含凭据；秘密在数据库加密且不自动回显；服务端故障不会改变正在运行的服务。
