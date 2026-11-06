# 静态文件部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 注册为 static 的项目用既有 publish/install/upgrade/rollback/status/logs 命令，把 ZIP/tar.gz 文件部署到指定目录，并支持可靠回滚。

**Architecture:** 项目元数据决定 Docker 或静态路径；Docker 保持原协议和运行管理器。静态路径增加有界归档处理与独立文件事务，复用平台的权限、版本、配置修订及回执。先准备完整版本文件，再原子切换目标目录链接。

**Tech Stack:** Python 3.10+ 标准库/PyYAML、Go/PostgreSQL、React/TypeScript、GitHub Actions；静态文件安装面向 Linux。

**Spec:** `docs/superpowers/specs/2026-10-09-static-deployment-design.md`

## Global Constraints

- 项目类型 `deployment_type: docker | static`，旧字段缺失按 docker，类型创建后不可改变；命令中仍显式指定项目名。
- Docker resolve 保持 schema_version=1、minimum_client_version=1.7.0；静态 resolve 使用 schema_version=2、minimum_client_version=1.13.0。
- CLI 功能版本暂定 1.13.0；开始实施前若主干已有更高版本，选择下一实际版本，并统一修改此计划、解析下限及分发资产。
- ZIP/tar.gz 保留包内相对路径，不剥离 dist/build 包装目录；仅普通文件/目录，脚本不执行。
- 可选 source commit 使用现有约定：空字符串或 `[a-f0-9]{40,64}`；同版本不同来源冲突。
- 压缩上限 256 MiB、展开内容合计 1 GiB、单文件 256 MiB、条目 50,000、路径 UTF-8 4096 字节；Docker 原有 10 MiB/5 MiB/1 MiB 限制不变。
- 静态文件 0644、目录 0755；拒绝路径穿越、绝对/盘符/反斜杠路径、重复、前缀冲突、链接、特殊文件、加密及损坏归档。
- 目标目录由项目/环境独占；首次允许不存在/空目录，不接管现有非空目录或未知链接。完整替换，保存 previous，同版本重试不丢失 previous。
- 远端 target_dir 优先；--target-dir 仅补充未配置的首次安装。已有绑定不自动迁移；升级未收到新目录设置时沿用本机绑定。
- 原包、状态、清单、日志和凭据位于公开 files 目录外；静态 resolve 不下发 runtime_env/install_params 秘密。
- 静态 stop/restart 和 Docker 专用安装参数返回适用性错误；本期不增加静态运行时配置、钩子或离线 --release 类型猜测。
- 当前分支开发；不修改旧的 `.superpowers/sdd/2026-10-07-runtime-config-hooks/`；本计划不执行正式发布、云服务器或管理服务更新。

## Review Focus

1. 项目在长时间上传中换组/被归档：发布事务复检当前归属，禁止越权落正式制品或更新 stable（Task 3）。
2. 两个项目使用不同 --root 安装到同一公开目录，或目录相互嵌套：只有一个所有者，后者不改已有文件（Task 4）。
3. 解压/切换途中 ENOSPC、EACCES 或 SIGKILL：旧版本保持可读，pending 可被 rollback 恢复（Task 4）。
4. Nginx 等非 root 用户读取静态文件：files 和必需父目录可遍历，原包、配置与状态不会出现在公开树（Task 4）。
5. 首次工作流发布成功但响应丢失后重试：按项目/版本/来源恢复原始产物，错误 commit 拒绝，stable 不被不同内容覆盖（Task 3、7）。

## File Structure

- 新建 `deployctl/static_archive.py` / `control-server/internal/artifacts/static.go`：格式识别、资源边界、归档路径验证；不混入 Docker manifest。
- 新建 `deployctl/static_state.py`、`static_target.py`、`static_runtime.py`：静态状态/清单、目录所有权/切换、部署事务，分别保持独立责任。
- 修改 domain/store/httpapi、platform_client/platform_management/cli：元数据、授权、协议及命令分发，避免重写 Docker Manager。
- 新建 `control-web/src/ReleaseUpload.tsx`：从 main.tsx 提取发布表单并按项目类型展示；沿用现有样式。
- 新建静态打包脚本、可复用工作流、调用模板、示例与 skill 参考；同步分发脚本中的资产清单。
- 新建 `tests/fixtures/static-archives/`：Python/Go 共用归档夹具；新建 Linux CLI/API 集成脚本并接入已有 CI。

---

### Task 1: 项目类型与目标目录配置

**Files:** 修改 `control-server/internal/domain/domain.go`、`store/store.go`、`store/project_management.go`、`store/group_environments.go`、`httpapi/projects.go`、`httpapi/group_environments.go`、`deployctl/platform_management.py`；测试新增 `domain/static_test.go`、`store/static_projects_test.go`、`httpapi/static_projects_test.go` 并扩展 `tests/test_platform_management.py`。

**Interfaces:**
- Go `DeploymentType(kind string) (string, error)`：空值归一为 docker，只接受 docker/static；Project.DeploymentType、Release.DeploymentType 为 JSON deployment_type。
- Go `ValidateTargetDir(path string) error`：允许空值用于清除默认，否则规范的单行 Linux 绝对路径，禁止 /、控制字符、反斜杠和 ..。
- DeploymentDefaults.TargetDir 为 JSON target_dir；Release.ArchiveFormat/ExpandedSize/EntryCount 为对应蛇形 JSON 字段。
- Python `validate_deployment_type(value: str | None) -> str`、`validate_target_dir(value: str) -> str` 位于新建 `deployctl/static_state.py`，后续任务共用。
- project create 接受 --deployment-type，update 不允许改变类型；配置 apply 的 deployment_defaults 接受 target_dir。

- [ ] **Step 1: 添加失败用例。** `TestStaticProjectNeedsNoImage` 断言 static + 空 image_repository 可创建；`TestLegacyProjectDefaultsDocker` 断言无类型旧 JSON 正常读取且仍验证镜像；`TestDeploymentTypeImmutable` 断言改变类型得到 ErrConflict。`TestStaticTargetDefaults` 断言组 target_dir 可保存、项目非空值覆盖、清除项目值后继承组、Docker 有效配置不返回 target_dir；组仍拒绝 Docker 资源默认值。Python 同步覆盖 create/show/config apply，保持 publisher/deployer 权限限制。

  代表性断言（domain/static_test.go）：
  ```go
  func TestLegacyTypeDefaultsDocker(t *testing.T) {
      kind, err := DeploymentType("")
      if err != nil || kind != "docker" { t.Fatalf("kind=%q err=%v", kind, err) }
      if ValidateTargetDir("/var/www/project-a") != nil { t.Fatal("valid target rejected") }
      if ValidateTargetDir("/") == nil { t.Fatal("root target accepted") }
  }
  ```
- [ ] **Step 2: 跑上述 Go/Python 用例，确认因尚无类型/目录字段失败。** Go store/httpapi 用 `CTL_TEST_DATABASE_URL` 真实 PostgreSQL，不以 skip 作为通过。
- [ ] **Step 3: 实现上述字段和验证接口。** 项目创建按类型补 Docker 仓库或保持 static 空仓库；在持有项目锁的更新中校验不可变类型。JSONB 读旧值按 docker，不批量重写历史数据。组 API 新增 target_dir 默认值及审计；effectiveRevision 根据真实项目类型合并并过滤配置。静态项目配置拒绝新增 Docker 专用配置；已有组 Docker 变量保留给 Docker 项目。Python 管理 defaults 单独验证 target_dir，再把余下 Docker 字段交给现有 protocol_defaults，避免本任务依赖后续静态 resolve 协议。
- [ ] **Step 4: 运行** `go test ./internal/domain ./internal/store ./internal/httpapi`（control-server），`python -m unittest discover -s tests -p test_platform_management.py -v`。期望全部通过，真实数据库用例未跳过。
- [ ] **Step 5: 提交** `feat: model static projects and deployment target directories`。

### Task 2: 跨语言安全归档与流式制品存储

**Files:** 新建 `deployctl/static_archive.py`、`control-server/internal/artifacts/static.go`、`tests/test_static_archive.py`、`control-server/internal/artifacts/static_test.go`、`tests/fixtures/static-archives/`；修改 `control-server/internal/artifacts/artifacts.go`。

**Interfaces:**
- Python frozen dataclass `ArchiveInfo(archive_format: str, sha256: str, size: int, expanded_size: int, entry_count: int)`。
- Python `inspect_static_archive(package: Path) -> ArchiveInfo`、`extract_static_archive(package: Path, destination: Path) -> ArchiveInfo`；提取入口要求新建/空目录且所有父路径无链接。
- Go `ValidateStaticFile(path string, expectedSHA string) (domain.Release, error)`；从已完成的临时文件识别 ZIP/tar.gz并遍历，不依赖后缀。
- Go `PublishTempFile(root, path string, release domain.Release) error`、`ArtifactPath(root string, release domain.Release) (string, error)`；摘要加真实后缀，旧 Docker 的空格式解释为 tar.gz。

- [ ] **Step 1: 写共同夹具及失败用例。** 有效 UTF-8/空格/字面量文件名 ZIP 与 tar.gz；拒绝 ../、/、C:、反斜杠、规范化重复、文件目录前缀冲突、软硬链接/设备/FIFO、空包、加密 ZIP、CRC/尾部损坏。生成边界用例断言 256*1024**2、1024**3、50_000、4096 的等值允许/超值拒绝，通过流式生成/临时文件避免测试自身大量内存分配。
- [ ] **Step 2: 运行** `python -m unittest discover -s tests -p test_static_archive.py -v`、`go test ./internal/artifacts`，确认新接口不存在/旧路径不支持静态包。
- [ ] **Step 3: 实现检查和提取接口。** 用 zipfile/tarfile 与 Go archive/zip、archive/tar，流式校验真实字节数及 checksum，不只信头部尺寸。规范化普通目录末尾 / 后进行全树碰撞检查，显式目录与隐式父目录可共存，重复显式条目拒绝。提取不使用无校验 extractall；写文件时排除链接，设置固定权限。临时制品原子归档，相同摘要重试验证已有文件，不覆盖不同内容；Docker Validate/PublishFile 保持兼容。
- [ ] **Step 4: 运行上述测试及** `python -m unittest discover -s tests -p test_release.py -v`；期望两种静态格式与原 Docker 包全部通过。
- [ ] **Step 5: 提交** `feat: validate and store bounded static archives`。

### Task 3: 平台发布、解析、下载及 CLI 流式传输

**Files:** 修改 `control-server/internal/httpapi/releases.go`、`store/store.go`、`deployctl/platform_client.py`、`deployctl/__init__.py`、`pyproject.toml`；新增 `httpapi/static_releases_test.go`、`store/static_releases_test.go`、`tests/test_static_platform.py`；扩展 `tests/test_platform.py`。

**Interfaces:**
- static resolve schema2 顶层含 deployment_type=static；release 含 id/version/package_path/sha256/archive_format/size/expanded_size/entry_count/commit；不含 image。configuration 含 id/revision/deployment_defaults，仅传 target_dir。
- Go 原 `PublishReleaseChecked` 签名保持，按锁定项目类型验证不可变版本元数据；仅 Docker 获取镜像仓库锁、调用 Registry 验证。
- Python `PlatformClient.publish(project, release_version, package, channel=None, verification_token=None, commit=None)` 保持旧位置参数；先读授权项目元数据，static 拒绝 Registry token。
- Python `PlatformClient.download_to_file(path, destination, expected_sha, max_bytes, progress=None) -> Path`：同源授权、拒绝重定向、完整响应、流式摘要校验。
- `download_release` 返回 Path，按已验证 resolution 分流；`recover_release` 返回旧 record/None，支持 static 来源校验及 .zip/.tar.gz 原字节恢复。

- [ ] **Step 1: 添加失败用例。** `TestStaticPublishResolveDownloadNoRegistry` 真实 HTTP 上传 ZIP/tar.gz，Verifier 一旦调用就失败；断言 metadata/下载 checksum 一致及 schema2 无 image/秘密。`TestStaticPublishReauthorizesCurrentGroup` 暂停上传、换组/归档后继续，断言无正式制品或 stable 更新。`TestStaticReleaseImmutable` 断言同字节/类型/来源重试复用 ID，不同来源/格式/摘要冲突。Python 本地 HTTP server 覆盖流式 >10MiB、截断/超限/重定向/错误摘要及恢复不同 commit 拒绝。
- [ ] **Step 2: 跑新测试，确认旧镜像要求/10MiB限制/固定 Docker resolution 使其失败。** 数据库并发用真实事务。
- [ ] **Step 3: 实现类型分流的 HTTP/Store 与客户端传输。** static 上传按 256MiB+64KiB multipart 上限写有界临时文件，字段独立1024字节限额；校验通过后填 project/version/commit。临时文件位于 ArtifactsDir 的私有准备区，允许同文件系统原子归档。事务复检归属与类型，冲突检查先于正式制品登记；所有失败清理当前临时文件。Docker commit 参数若提供必须等于 manifest。static 返回 schema2 与新版本下限，同时将源代码 CLI/pyproject 版本提高到该下限；Docker 响应字段严格维持 schema1。下载校验 Content-Length/真实长度及摘要。multipart 通过可重开文件的分块 iterable 发送，设置精确 Content-Length，不拼接完整大包；错误输出不暴露 Token。
- [ ] **Step 4: 运行** `go test ./internal/store ./internal/httpapi`、`python -m unittest discover -s tests -p 'test*platform*.py' -v`。期望权限、版本、流式 static 和 Docker 全部通过。
- [ ] **Step 5: 提交** `feat: publish and resolve static releases through the platform`。

### Task 4: Linux 静态目录事务和离线回滚

**Files:** 新建 `deployctl/static_target.py`、`deployctl/static_runtime.py`、`tests/test_static_runtime.py`、`tests/test_static_target.py`；扩展 `deployctl/static_state.py`。复用 `runtime.service_lock/atomic_json`，不改 Docker state 的形状。

**Interfaces:**
- `normalize_static_state(data: dict | None, app: str, env: str) -> dict`；schema1 + deployment_type=static，current/previous/transaction/target_dir，引用含 version/package_sha256/tree_sha256/目录身份及 management_source。
- `build_tree_manifest(files: Path) -> dict`、`verify_tree(files: Path, manifest: dict) -> None`：按 UTF-8 相对路径排序，绑定目录/文件类型、大小、内容摘要和规范权限。
- `target_lock(target: Path)` contextmanager；`validate_target(target: Path, root: Path, config_root: Path, owner: dict) -> None`；`switch_target(target: Path, files: Path) -> None` 使用同父临时链接与 os.replace。
- `StaticManager(root='/opt/deployments', config_root='/etc/deployctl', progress=None)`；`deploy(app, env, package, resolution, upgrade=False, target_dir=None) -> dict`、`rollback(app, env) -> dict`、`operate(app, env, operation, tail=100) -> str`。

- [ ] **Step 1: 写失败测试。** 首次装 v1、升级 v2 断言旧文件消失、回滚还原 v1，同版本重试保留 previous。断言缺失/根目录/链接父路径/已有非空目录/篡改文件树拒绝。双进程不同 --root 抢同一目录，只允许一个成功；嵌套目录冲突同样拒绝。ENOSPC/EACCES及各 pending/switch/save 点异常后旧树可读。Linux 子进程 SIGKILL 后 rollback 恢复，首次中断无旧版本时仅清理本次已绑定候选。非 root 读取公开 index.html 成功；目标树不存在原包/manifest/state/秘密。
- [ ] **Step 2: Linux 跑** `python -m unittest discover -s tests -p 'test_static_runtime.py' -v` 与 target 测试；确认缺少 StaticManager 失败。Windows 仅跳过明确需要 POSIX 的测试，跨平台归档/state 测试照常运行。
- [ ] **Step 3: 实现静态 state/target/manager。** 目标父目录内使用由规范目标身份派生的固定锁/所有权记录，位置不受 --root 改变影响，0700/0600 元数据；登记所有者包含 root/project/environment/规范目标路径，检查父子目标所有权。公开链接指向 managed releases 下的 files；必需遍历父目录0755、敏感文件0600，不放秘密，不擅自 chmod 已有外部父目录，无法遍历时在切换前报错。先完整验证候选树，再写 pending、原子切换、校验并提交；关键目录/文件 fsync。首次空目录不能直接被 symlink replace：在 pending 中记录空目录身份，确认仍为空后移开到同父恢复位置，再放入链接；失败恢复空目录。未知链接绝不解引用后覆盖。失败回切旧链接，恢复失败保留 pending；upgrade 拒绝 pending。rollback 只用本地已验证绑定/缓存，断网仍工作；初装失败仅移除本次创建对象。
- [ ] **Step 4: Linux 运行全部 static runtime/target/state 测试。** 期望原子切换、跨 root 冲突、中断恢复与非 root 文件读取通过；记录实测能力与未具备环境的验证项，不将跳过算成功。
- [ ] **Step 5: 提交** `feat: deploy static files with atomic switching and rollback`。

### Task 5: 统一 CLI 命令和本机状态分发

**Files:** 修改 `deployctl/cli.py`；新增 `tests/test_static_cli.py`，扩展 `tests/test_runtime_cli.py`、`tests/test_cli_identity.py`。

**Interfaces:**
- install/upgrade 新增 --target-dir；publish 新增 --commit。项目类型从 resolution 获取，不新增 static/docker/website 命令前缀。
- 新 `local_deployment_type(root: str, app: str, env: str) -> str` 与已有 local_environment 配合；读取已有 static 标记，无标记走旧 Docker。发现未知类型或混合状态失败，读状态不自动迁移。
- Docker Manager 延迟构造；static install/upgrade 调 Task 4 的接口，无 registry_config、DockerDriver、hooks 或 Docker env merge。

- [ ] **Step 1: 写失败测试。** 同样的 install project-a --prod 自动 static 分流；无远端目录时 --target-dir 生效，远端优先且升级改目录报迁移错误，缺省升级沿用已存目录。static 状态下统一 rollback/status/logs 离线分流。Docker 所有调用设为一旦访问就失败，static 仍成功。--env-var/--set/--unset-env/--port/--bind、stop/restart 及 static --release 返回明确适用性错误；Docker --target-dir 也报错，防止误解。保留 --quiet、成功失败回执和 env 默认选择。
- [ ] **Step 2: 跑** `python -m unittest discover -s tests -p test_static_cli.py -v`；期望旧 parser/固定 Manager 使测试失败。
- [ ] **Step 3: 实现 CLI 分流。** 先解析目标、验证协议与类型、检查参数适用性，再下载和构造对应 Manager。本地命令沿用本机已安装身份，平台失败不影响 rollback。static 成功输出 deployed 和 target_dir，不称 running；status 展示文件版本/目录/pending，logs 展示部署事件，不暗示外部 Web 服务健康。
- [ ] **Step 4: 跑 static CLI 和** `python -m unittest discover -s tests -p test_runtime_cli.py -v`、`python -m unittest discover -s tests -p test_managed_runtime.py -v`；期望统一命令及 Docker 生命周期/回执回归通过。
- [ ] **Step 5: 提交** `feat: dispatch unified ctl commands by project deployment type`。

### Task 6: 管理台静态项目、上传与配置

**Files:** 修改 `control-web/src/api.ts`、`main.tsx`、`projectForm.ts`、`environmentForm.ts`、`ProjectGroups.tsx`；新建 `ReleaseUpload.tsx`、`releaseUpload.ts`、`releaseUpload.test.ts`、`control-web/tests/static-deployment-real.spec.ts`，扩展已有项目/环境表单测试。

**Interfaces:**
- TypeScript `DeploymentType = 'docker' | 'static'`；Project.deployment_type 兼容缺省 Docker，Release 新增归档统计，Defaults.target_dir。
- `ReleaseUpload({project, done}: {project: Project; done: () => void})` 替代现有仅 slug 的 UploadRelease；Docker 原表单保持实际行为。
- `releaseUpload.ts` 的 `uploadRelease(project: Project, file: File, version: string, channel: string, commit?: string): Promise<Release>`；FormData/XHR 保留上传进度，哈希通过分块读取计算，不一次 arrayBuffer 载入256MiB。

- [ ] **Step 1: 写失败测试。** 创建 static 不要求镜像仓库；编辑无法改变类型；旧项目显示 Docker。静态上传允许 ZIP/tar.gz/256MiB、显示版本/来源，隐藏 Registry Token；失败不关闭表单且清晰提示。静态环境展示 target_dir/目标版本，不展示 Docker 变量输入；组可保存/清除默认目录，项目覆盖后正确显示继承。浏览器真实 API 创建项目、上传、设置目录与版本并再次读取一致；验证无配置/类型绕过权限。
- [ ] **Step 2: 运行** `npm test` 与新增 Playwright 用例（真实服务与 PostgreSQL），确认原表单失败。
- [ ] **Step 3: 实现类型相关表单与上传。** 沿用现有组件/样式、dirty guard、冲突重载和错误提示；static 表单只提交适用字段，列表与详情显示格式/摘要/大小。浏览器流式 SHA256 使用明确可审阅实现/锁定依赖，WebCrypto 若只能整包计算则不采用；server 仍独立校验，浏览器校验不当作授权。
- [ ] **Step 4: 运行** `npm test`、`npm run build`、`npm run test:browser`（包含 static 真实 API 与现有 Docker 配置/上传/权限用例）。期望全部通过。
- [ ] **Step 5: 提交** `feat: manage static projects and releases in the control UI`。

### Task 7: 静态构建流水线、接入文档与完整验收

**Files:** 新建 `.github/workflows/build-static-release.yml`、`scripts/package_static.py`、`templates/static-release.yml`、`examples/static-project/index.html`、`docs/static-deployment.md`、`skills/team-deploy/references/static-deployment.md`、`tests/test_static_workflow.py`、`tests/static_control_plane_integration.py`；修改 `.github/workflows/verify.yml`、`control-plane.yml`、`scripts/build_release_assets.py`、`build_agent_assets.py`、`skills/team-deploy/SKILL.md`、README 及相关分发测试。

**Interfaces:**
- `package_static(directory: Path, output: Path, archive_format='tar.gz') -> ArchiveInfo`（scripts/package_static.py）；只打产物目录内容、排序、固定权限/时间/gzip头、拒绝链接和资源超限。
- workflow_call inputs: platform-repository/platform-ref/project/version/server/build-command/output-directory、可选 test-command；secret CTL_PUBLISH_TOKEN、可选 PLATFORM_READ_TOKEN。outputs: application/version/sha256/archive_format；未发布 GitHub Release 时不伪造 release_url。
- workflow 检出业务 tag 与平台固定引用；执行 test/build，由项目 build-command 自行安装依赖，打包后同一 ctl publish --commit。平台凭据用于恢复/发布，不能读生产秘密，也不登录 Registry。

- [ ] **Step 1: 写失败用例。** 相同目录不同 mtime/顺序/宿主权限产出相同字节；打包输出位于输入目录内部时明确拒绝，不能自我归档；顶层 wrapper 不猜测剥离。工作流重试先通过 recover_release 验证来源并恢复，无错 commit 重建覆盖。真实 API 集成：注册 static、发布 v1、配置目录、CLI install、发布 v2/upgrade、断平台网络后 rollback/status/logs、receipt 检查；真实 Docker 集成保持通过。zipapp + skill 包内含新模块/模板/参考，checksum 与版本一致。
- [ ] **Step 2: 跑新打包/工作流/分发测试，确认新工作流和资产尚缺失。** 不为纯说明文字写实现镜像测试。
- [ ] **Step 3: 实现打包与工作流，更新文档/skill/资产及版本。** 新静态调用模板单独提供，旧 Docker init 不改变；说明平台注册类型、配置目录、统一命令、包根目录、公开文件风险、故障回滚与 Nginx/Caddy 对接。重试先恢复已登记版本，若无版本再构建/发布。配置 token-file 保持私有权限；构建参数通过项目脚本/工作流输入，部署不重新构建。仓库 skill 更新遵守 skill-creator/writing-skills；本机 skill 更新使用既有授权、备份并比较资源，不写入账号凭据。
- [ ] **Step 4: 构建分发资产并运行完整验证。** `python scripts/build_install_script.py --check`、`python scripts/build_release_assets.py`、`python -m unittest discover -s tests -v`、`python -I -S dist/deployctl.pyz --version`、`python scripts/build_agent_assets.py --output dist/agent-assets`；actionlint 两种工作流；`go test -race ./...`、`go vet ./...`（真实 PostgreSQL与Registry）；`npm test`、`npm run build`、`npm run test:browser`；Linux `python tests/static_control_plane_integration.py`、`python tests/control_plane_integration.py`、`python scripts/docker_integration.py`、`bash scripts/installer_smoke.sh`。CI 对静态事务要求真实 Linux执行，不以 Windows skip 替代。未具备依赖的验证列为未执行，不能宣称完成验收。
- [ ] **Step 5: 提交** `feat: deliver static builds with reusable workflows and agent guidance`。完成分支审查、修复实际问题后重跑受影响验证；报告已实现功能和验证证据。本次不自动打正式 tag 或更新云端服务。

## Execution Handoff

本计划等待用户审阅；推荐当前会话由主代理逐任务实现，保持用户已有的当前分支开发要求。实现前读取 spec 与本计划，逐任务执行测试/实现/验证/提交；完成后独立审查整个变更。正式发布和管理服务更新另行执行已有或新增的具体授权，不与本地验证混同。
