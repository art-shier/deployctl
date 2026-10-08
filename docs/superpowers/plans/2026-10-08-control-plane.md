# ctl 服务端与管理台实施计划

> **For agentic workers:** Use superpowers:executing-plans for inline execution, or superpowers:subagent-driven-development if the user selects delegation. Tasks use RED/GREEN verification and one fresh whole-change review at delivery.

**Goal:** 注册项目、发布镜像与标准包，在管理台保存环境配置，通过ctl install项目名安装并兼容旧方式。

**Architecture:** Go API+PostgreSQL保存元数据、加密配置和权限；成熟OCI Registry保存镜像，私有文件目录保存标准包。React管理台调用同源API。Python CLI解析固定版本/修订，沿用受检快照事务与Compose执行。

**Tech Stack:** Go、PostgreSQL、React/TypeScript/Vite、Distribution Registry、Python>=3.10、Docker Compose>=2.30。

**Spec:** `docs/superpowers/specs/2026-10-08-control-plane-design.md`。

## Global Constraints

- 功能基线9e62828；本计划在独立feat/control-plane分支。两个现有PR和旧Release不修改或自动合并。
- 首版单组织，web远程部署/常驻Agent/多机编排不在范围；平台单独Compose引导，业务标准包v1/v2保留。
- Runtime与安装参数都由管理台最高优先；host_port/bind显式CLI覆盖管理台默认，并保留当前绑定。
- 每次部署固定同一release+configuration revision；pre回读后再次叠加相同远端层。local overrides不能含remote值。
- 参数限制精确沿用128项/128字符键/4096字符值/64KiB UTF-8；秘密无日志、无公开资产、无URLToken。
- CLI1.7.0与server0.1.0是拟交付版本，不创建标签、不发布、不开生产数据库直到相应授权。
- 后端、CLI和前端任务共享本文契约；自测真实网络边界的失败和恢复，不以mock代替Registry/Docker最终验收。

## Review Focus

- 同版本重复发布或并发发布：相同内容幂等，不同内容409，版本不可覆盖。
- 注册仓库/配置/包篡改与跨项目请求：身份边界、同源下载、digest和包成员校验不得被绕过。
- 远端最高层被pre或persisted overrides污染：删除远端键可回退，回滚恢复实际旧值。
- 发布/配置变更发生在安装期间：本次固定解析结果不漂移，不伪报服务或配置成功。
- 秘密keep/empty/remove、Token撤销、错误/日志：不丢失密码、不将占位符保存、不泄露值。

## 文件边界

- `control-server/cmd/ctl-server/main.go`：serve/bootstrap入口。
- `control-server/internal/config/`：服务配置与受保护密钥加载。
- `control-server/internal/domain/`：Project/Release/Revision/Token/Resolution定义及校验。
- `control-server/internal/store/`：PostgreSQL迁移、事务与库接口。
- `control-server/internal/auth/`：Token哈希、owner会话、授权、Registry RS256签发。
- `control-server/internal/secrets/`：AES-GCM配置加密。
- `control-server/internal/artifacts/`：标准包验证与文件原子发布。
- `control-server/internal/registry/`：OCI manifest存在/digest验证。
- `control-server/internal/httpapi/`：API与静态管理台，统一脱敏错误。
- `control-web/src/`：登录、项目、版本、环境、凭据、安装记录及审计页。
- `deployctl/platform_client.py`、`platform_credentials.py`：HTTPS API/同源下载/私有客户端凭据。
- `deployctl/runtime.py`、`runtime_config.py`、`runtime_snapshot.py`、`state.py`：远端层、来源元数据、兼容快照与绑定。
- `control-deploy/`：Dockerfile、Compose、引导脚本与运维文档。
- `tests/control_plane_integration.py`、`.github/workflows/control-plane.yml`：真实PostgreSQL/Registry/Docker集成。
- Notes仓库：部署hooks、发布工作流、对应回归与文档；复用当前feat/team-deploy工作区，独立提交。

## Task 1: Go领域、持久化与认证

**Interfaces:** Domain中公开Project、Release、ConfigurationRevision、Principal、Resolution；JSON字段按Spec。Store提供CreateProject/ListProjects/GetProject、SaveRevision(expectedRevision)/GetRevision、CreateToken/Authenticate/RevokeToken、PublishRelease/Resolve、SaveReceipt/ListReceipts、Audit。Resolve在一个事务中选择版本与修订；配置密文由Secrets.Seal/Open处理。

- [ ] 写领域/加密/授权失败测试：非法slug和环境、参数边界、AAD不匹配、密文篡改、publisher读配置、跨项目和环境、被撤销Token拒绝。先运行`go test ./internal/domain ./internal/secrets ./internal/auth`观察RED。
  TestValidateValuesLiteralAndLimits断言128项/4096字符可用、129项/4097字符拒绝、`" $ # = "`与空值原样；TestCiphertextBoundToRevision断言错误环境/修订及篡改Open失败；TestPublisherCannotReadProduction/TestDeployerProjectEnvironmentScope/TestRevokedTokenRejected断言403/401且无原始值。
- [ ] 实现领域类型、字面值限制、AES-256-GCM及最小权限Principal.Can(action,project,environment)。HashToken不存明文；会话限时且校验Origin。
- [ ] 写Store事务测试：revision乐观锁409、并发版本发布幂等/冲突、Resolve固定目标及配置ID；迁移空库并重跑不破坏数据。
  TestRevisionConflictPreservesSecret断言expected_revision=1在当前2时失败且secret未改；TestPublishIdempotency断言同version/SHA成功、异SHA409；TestResolvePinsRevision断言读取后修改环境仍不改变已返回ID与值。
- [ ] 实现迁移与PostgreSQL Store；API只访问Store，不直接拼接SQL。SQL值参数化，配置AAD绑定项目/环境/修订。
- [ ] `go test ./...`和`go vet ./...` GREEN；CI临时PostgreSQL执行真实Store测试。提交独立可运行后端基础。

## Task 2: 标准包发布、Registry与API

**Interfaces:** artifacts.Validate(reader,expectedSHA,project)返回ValidatedPackage；registry.CheckManifest(image,allowedRepository)验证真实digest；POST releases返回Release，POST resolve返回Spec固定Resolution；GET artifacts提供同源、授权的标准包。

- [ ] 写HTTP回归并观察RED：项目注册、秘密脱敏与keep/set/remove、配置409、auth错误、跨项目Token、发布包重复/缺失/篡改/超限、版本未完整发布无法解析。
  TestSecretKeepSetRemoveAndEmpty断言keep不能带占位符value、set(" ")/set("")保持字面值、remove恢复缺失；TestPartialPublishNotInstallable/TestRetiredReleaseRejected断言resolve失败；TestArtifactOriginAndProjectBoundary断言未授权跨项目无包内容。
- [ ] 使用现有Python packager生成真实v1/v2/refresh测试fixture；服务端校验其包，与现有客户端共用schema和golden fixture，不能放宽tar/hook/digest校验。
- [ ] 实现文件暂存与原子发布，版本发布/配置/resolve/凭据/receipt/audit API；统一错误不打印敏感底层错误。
- [ ] 实现Registry bearer challenge所需RS256签发；证书/issuer/audience/expiry/scope匹配Distribution配置。
- [ ] 临时真实Registry验证push/pull、manifest/index检测和跨项目/越权push拒绝，提交API和制品接口。

## Task 3: CLI平台解析与配置事务

**Interfaces:** PlatformClient.resolve(project,environment=None,version=None)->Resolution；download_release(resolution,cache)->Path；Credentials.load/save；Manager.deploy新增managed_runtime、managed_params、management_source、deployment_defaults可选参数，旧调用不变。

- [ ] 写CLI凭据、HTTPS/重定向/同源制品测试并观察RED。login拒绝链接/非私有文件，不在错误中回显Token；HTTP仅本地测试允许。
- [ ] 实现login/publish、--prod/默认环境/--version、--release旧模式与--with-platform-config；冲突参数拒绝，旧--release未登录仍可用。
- [ ] 写事务测试并观察RED：远端覆盖本次env-var/set、pre回读后仍覆盖、远端删除键回退本地值、local overrides没有remote值、未配置/空值保持区别、最终合并超限拒绝。
  test_managed_layer_survives_pre_refresh断言pre写APP_ORIGIN仍以远端值启动、远端ADMIN_EMAIL覆盖--set；test_removed_remote_key_restores_local_override断言远端删LOG_LEVEL后恢复此前local debug，overrides无其他远端键；test_managed_resource_rollback断言回滚恢复旧memory/cpus与端口。
- [ ] 修改merge_runtime_values并增加merge_install_params，更新Manager两阶段合并；.management.json包含非秘密来源并纳入可选快照哈希，旧快照仍能加载。
- [ ] 写失败/恢复测试：resolve下载失败不动服务、安装期间远端变化不影响固定结果、post失败恢复旧快照、回滚不调用平台、回滚缓存缺失明确失败。
- [ ] install/upgrade支持端口与绑定；CLI显式值覆盖默认，未传保留现有绑定；动态资源受白名单限制。补报失败不改变已成功部署结果。
- [ ] 完整Python suite、schema、分发zipapp、Linux真实Bash与Docker GREEN；提交CLI平台模式。

## Task 4: 管理台

**Interfaces:** React调用Task2同源API；只消费脱敏配置读接口，keep/set/remove写入契约与expected_revision一致，不持久化owner明文Token。

- [ ] 读取frontend-design/application-ux技能，确定管理台视觉和工作流；界面按Spec页面/状态实现，不在产品里展示底层实现流程。
- [ ] 写客户端/表单测试：Cookie登录状态、secret keep不提交占位符、显式空值、remove、409保留草稿、Token仅创建时显示。
  environmentForm.test验证keep请求没有value、set空值带value=""、409保留未保存输入；session.test验证退出清理会话、401显示登录；tokens.test验证创建成功仅本次展示且列表不返回明文。
- [ ] 实现登录、项目列表与详情、版本、环境配置、安装参数、部署默认值、Token、安装记录与审计页面；标签、可访问表单、错误反馈完整。
- [ ] `npm test`、`npm run build`、TypeScript校验GREEN。浏览器连接实际测试API验证注册、保存环境、生成Token和选择版本，不仅使用静态mock。
- [ ] 由API提供构建后的管理台静态资源，提交管理台。

## Task 5: 平台引导、Notes接入与整体交付

**Interfaces:** control-deploy提供API/Registry/持久制品卷启动；平台发布Token由项目管理员创建；Notes source保持标准包且pre优先使用有效DATABASE_URL。

- [ ] 写引导回归：重复安装保留密钥/数据、权限错误拒绝、缺少数据库或密钥不启动、无真实凭据写入Git。引导生成私有密钥/owner凭据，并输出访问地址。
- [ ] 实现非root API镜像、Registry证书验证、持久化目录、外部或独立PostgreSQL配置、健康检查与备份恢复文档；不得修改宿主机其他站点。
- [ ] Notes先写RED：管理台提供DATABASE_URL时不依赖ConfigHub CLI/Token；缺少时旧生成流程可用；只读DB检查、缺失DB拒绝和post bootstrap保持。
- [ ] 改Notes pre并再生hook，流水线测试后推送镜像与ctl publish；保留旧GitHub发布入口，publisher不访问prod配置。
- [ ] CI真实端到端：注册Notes fixture、创建publisher/deployer、推镜像/包、创建prod配置、CLI install、管理台覆盖CLI值、改配置upgrade、安装中途改配置固定修订、失败回滚、旧--release离线入口、Token撤销/跨项目Registry拒绝。
- [ ] 一次独立整体代码审查；Important/Critical用失败回归修复，完整suite GREEN。更新CLI安装、项目接入、平台引导和Notes运维说明。
- [ ] 交付受检源码PR和真实CI证据。提出具体新版本发布方案；没有发布授权不创建tag/Release，没有目标和部署授权不操作生产服务器。

## 顺序与执行选择

Task1→2→3；Task4消费已固定的API；Task5整合所有组件。建议本会话直接实现，避免同一认证/配置契约在多个实现者之间反复解释；整体独立审查保留。用户如选择并行子Agent，Task4可在Task2接口固定后与Task3并行，不能并行修改Python事务核心。

本计划与技术设计一并交付，代码实现尚未开始。审阅时重点确认：首版无网页远程执行、管理台配置优先、端口按主机覆盖、外部镜像通过新版本登记、Registry与制品持久化、CLI兼容旧模式。
