# ctl 管理服务与平台模式

本版本包含 CLI **1.7.0**、管理服务 **0.1.0**，统一通过ctl工具版本的Release分发。旧ctl1.5.0不支持平台命令，需先升级。管理服务与业务服务各自部署，管理服务故障不影响已有容器。

平台包含 Go API / React 管理台、Distribution Registry、标准发布包目录和 PostgreSQL。单组织自托管；网页管理配置与版本，部署由目标服务器上的 ctl 执行。安装记录是客户端上报的历史结果。

## 引导管理服务

要求 Linux、root、Docker Engine、Docker Compose >=2.30、Python >=3.10。公开Release安装无需Git、源码或平台登录；第一次登录凭据在服务启动时生成。

```bash
set -o pipefail
curl --fail --silent --show-error https://raw.githubusercontent.com/art-shier/deployctl/v1.7.0/install.sh \
  | sudo bash -s -- --version v1.7.0

RELEASE_URL='https://github.com/art-shier/deployctl/releases/download/v1.7.0/ctl-platform-v1.7.0.tar.gz'
sudo ctl server install --release "$RELEASE_URL"
```

已由安装器管理的CLI可以用 `sudo ctl self-update --version v1.7.0` 更新原目录；用户目录安装时用原用户和原命令路径。`server install`与普通业务 `install` 独立，管理API、Registry和可选PostgreSQL由固定Compose启动。

工具自动下载相邻 `.sha256` 并校验，可用 `--sha256 <独立取得的摘要>` 显式指定。包只含引导脚本、配置生成器、Compose及清单，清单固定真实镜像digest和成员SHA256；不含密码。CLI限定成员/大小、不直接tar解压、使用私有目录、检查父目录可信权限，拒绝路径链接和被修改的缓存。操作获取主机级及实例级锁；超时/中断先停止引导进程组再释放锁。固定Compose项目ctl-platform每个Docker主机只支持一个实例，--home用于指定其存储位置，发现其他home的已有实例时拒绝替换。

默认API origin为 `https://ctl.shier.art`，Registry host为 `ctl.shier.art`，实例目录 `/opt/ctl-platform`。首次可传 `--origin`、`--registry-host`、`--api-port`、`--registry-port`、`--home`、`--database-url-file`；已有实例目标不会自动变更。默认启动独立PostgreSQL；外部数据库使用私有URL文件。

升级管理服务先按下文备份，再执行：

```bash
sudo ctl server upgrade --release "$NEW_VERIFIED_RELEASE_URL"
```

同版本同包可重复upgrade；install拒绝已有实例，源码引导过的实例使用upgrade接入。退出失败时保留 `server-state.json` 的pending记录，修复原因后用原命令/原发布包重试，不能换包绕过pending。引导保留密钥和数据，不提供数据库/平台镜像的自动事务回滚；失败不能报告为已升级成功。

仍支持从经过审核的源码构建（另需Git），在仓库根目录运行：

```bash
sudo bash control-deploy/bootstrap.sh \
  --origin https://ctl.shier.art --registry-host ctl.shier.art
```

脚本构建 API/管理台镜像，初始化 `/opt/ctl-platform`，生成独立的配置加密密钥、Registry 签名私钥/验证证书、owner 凭据和数据库连接，启动 API、Registry 和独立 PostgreSQL。重复运行保留密钥、密码和数据。使用预构建镜像可加 `--image <经过验证的镜像地址>`；不会下载未发布的假定版本。

默认管理入口监听本机 **8080**，Registry入口 **5000**；通过 `--api-port` / `--registry-port` 修改。两个端口都由API网关接收，底层Registry只在Compose内部开放。绑定在127.0.0.1，需自行将HTTPS反向代理指向这两个端口、配置DNS和证书。Registry入口允许大请求体，保留Authorization / WWW-Authenticate头，blob传输超时按镜像大小调整（网关最长60分钟）。API发布包不超过10 MiB；网页镜像上传路径 `/api/v1/projects/*/images/upload` 需允许2 GiB请求体及至少16分钟代理读写超时。脚本不修改现有网站。

实例配置在私有 `instance.json` / `compose.env`；重复引导拒绝自动更换已记录的域名、端口或数据库目标。如需变更，停止平台、备份，明确编辑这些实例设置后重启。业务项目的域名与 ctl 的域名独立。

共用 `ctl.shier.art` 时，将整个域名（包含 `/v2/`）反向代理到127.0.0.1:8080即可，额外Registry入口不必公开。DNS解析到主机，HTTPS证书由现有代理配置。发行流水线验证镜像可匿名按digest拉取后才发布服务端包。

### 使用外部 PostgreSQL

预先创建专用数据库和账号，把完整连接 URL 写入 root 所有、权限600的文件（用户名、密码含特殊字符时进行 URL 编码）。然后首次引导：

```bash
sudo bash control-deploy/bootstrap.sh --database-url-file /root/ctl-database.url \
  --origin https://ctl.shier.art --registry-host ctl.shier.art
```

不启动平台内置 PostgreSQL。URL复制到私有 `keys/database.url`，API通过文件读取。已有实例不会自动切换数据库。外部连接建议使用 `sslmode=verify-full` 和可信证书链；按数据库管理员提供的连接要求配置。

## 注册项目和配置环境

管理员从 `/opt/ctl-platform/keys/owner.token` 获取首次登录凭据，打开自己的 ctl HTTPS 地址登录管理台。owner token 不进入浏览器存储；登录建立8小时的 HttpOnly 会话。请单独保存 owner 凭据。

1. 注册项目 `notes`，默认环境 `prod`，托管镜像路径默认为 `ctl.shier.art/notes`。可以填写登记的外部镜像仓库。
2. 环境配置中添加业务变量（例如秘密 `DATABASE_URL`）、安装参数（例如 `ADMIN_EMAIL`）和部署默认端口/内存/CPU。
3. 创建 `publisher` 凭据交给该项目 CI；创建仅允许 `notes/prod` 的 `deployer` 凭据交给生产安装主机。明文只展示一次。publisher 无法获取生产配置。
4. 保存配置创建新修订；发生冲突时保留草稿，重新加载对比。秘密可保持、替换（包括空字符串）或删除。

项目默认环境自动创建空修订1并指向 `stable`。环境名称与版本独立；发布完整版本并显式设置 stable 后才可安装。可以将环境固定到某个可安装版本。停用版本禁止新安装，保留回滚所需制品。首版不自动清理 Registry / 发布包。

## 镜像管理

项目详情的独立“镜像管理”页显示镜像数、标签数、未关联版本数，支持搜索和每页20个镜像的分页。同一个 Digest 的多个标签合并为一条镜像；数量统计的是当前带标签的 manifest/index，不包含无标签的历史内容和独立镜像层，也不将列表第一页当成仓库总数。服务端逐页读取 Registry，最多读取10000个标签，超限或读取失败时明确报错，不返回不完整统计。详情读取架构、完整镜像地址及去重压缩内容大小；该大小不等于磁盘实际占用空间。

托管仓库支持两种手动上传入口：

- **上传镜像**：选择 `docker save --output image.tar your-image:local` 导出的单镜像 `.tar`，指定新标签。文件最大2 GiB，展开的镜像层累计最大8 GiB，上传与入库最长15分钟；多镜像归档、多架构发布及更大的镜像使用 docker push。页面显示上传百分比，传完后显示校验/入库状态，只有仓库校验成功才显示完成。
- **手动推送**：页面给出本项目的 docker login/tag/push 命令；使用项目 publisher 凭据交互登录。流水线仍使用原标准 Docker push 流程。

上传由Go OCI客户端完成，不执行镜像，不挂载Docker socket。归档流式写入私有 `artifacts/.uploads`，不在内存缓冲整个文件、不解压到宿主机目录；验证路径、成员、配置、实际层哈希、压缩展开限制及仓库目标。普通结束或取消后清理文件；进程异常留下的旧普通上传文件在下一次上传时清理（超过24小时）。每个服务实例只处理一个镜像上传任务，其他请求收到忙碌提示。上传失败/取消可能留下未引用的 Registry 内容，刷新确认结果后可重试；同标签同内容重试允许，不覆盖同标签的不同镜像。上传镜像不自动发布或推进stable，仍需在“版本”关联标准部署包。

归档内的镜像层必须使用一致的压缩模式（全未压缩或全压缩）；混合模式、无法正确声明的压缩类型返回明确提示，改用docker push，避免SDK错误转换层内容。归档校验和digest计算完成后才签发本次HEAD验证凭据，推送时按请求重新签发短期凭据，不因大文件校验耗时而提前过期。

**删除**仅管理员通过API/管理台执行，只支持托管仓库，须确认Digest及全部受影响标签。所有项目共享该仓库的发布版本都受保护，包括已停用、用于回滚的版本；多架构索引引用的子manifest也不能删除。发布的镜像校验与登记、手动上传、删除使用同一PostgreSQL仓库锁，避免并发发布登记一个刚被删除的镜像。删除行为写入审计。CLI/CI的普通publisher/deployer凭据不具备此删除权限。

普通Docker推送的manifest PUT也通过网关获取同一仓库锁，先验证短期JWT的签名、范围、issuer/audience与期限，读取有界manifest后才加锁；blob上传保持流式转发。底层Registry不可向外直接暴露，否则会绕过这项并发保护。公共OCI DELETE禁止直达Registry，删除统一走管理接口；管理API自身上传/删除在已持锁时访问内部Registry，避免锁重入。升级已有实例时使用更新的Compose重新启动，域名和原入口端口不变。

删除manifest会让全部对应标签失效，**不会立刻回收镜像层占用的磁盘空间**。维护时停止API写入，暂停Registry写入并备份后，用Distribution的 `registry garbage-collect /etc/docker/registry/config.yml` 回收无引用内容；不要使用 `--delete-untagged`，它可能影响多架构子manifest及无标签的回滚版本。维护回收需要按实际配置/备份制度执行，管理台不自动运行垃圾回收。

外部仓库在该仓库管理端上传/删除，管理台只读取镜像和关联版本。私有外部仓库的短期pull证明仅用于本次刷新/详情，读取后清空，不保存为长期凭据。

## CI 发布到平台

CI使用普通 Docker login / build / push，推送到项目允许的仓库并获得实际 manifest/index digest。然后 `ctl package` 生成固定 digest 的标准包、`ctl publish` 上传包并提交版本。只有镜像和包都通过验证，版本才变成可安装。

```bash
# 配置文件目录700、token文件600；TOKEN_FILE由 CI 的秘密存储生成。
ctl login --server https://ctl.shier.art --token-file "$TOKEN_FILE" --client-config "$CLIENT_FILE"
ctl package --config deploy/deployment.yaml --image "$IMAGE_WITH_REAL_DIGEST" --version "$PROJECT_VERSION"
ctl publish notes --version "$PROJECT_VERSION" --package "dist/notes-${PROJECT_VERSION}.tar.gz" \
  --channel stable --client-config "$CLIENT_FILE"
```

`--channel stable` 在完整发布事务成功后推进指针；省略则不修改指针。同版本同内容允许重试，内容不同返回409。更换镜像地址/digest应登记新版本；不能改写旧包。公开外部Registry（包括GHCR）自动进行匿名Bearer认证。

外部私有仓库发布时，提供仅用于这次服务端校验的短期repository/pull Bearer，例如`ctl publish ... --registry-token-file /私有路径/pull-token`。文件须当前身份所有、权限600。服务端只向项目登记的镜像仓库发送此证明，拒绝重定向，不保存、回显或转交安装主机；管理台登记/刷新镜像也可输入一次性验证Token。托管Registry无需此参数。外部Token从该仓库的认证服务获取；不能把ctl平台Token或长期用户名/密码当成验证Token。安装主机继续使用它已有的Docker凭据。

Notes 流水线默认使用管理服务 `https://ctl.shier.art` 和镜像仓库 `ctl.shier.art`。仓库变量 `CTL_SERVER_URL`、`CTL_REGISTRY_HOST` 可分别覆盖，未配置或为空时各自使用默认值；秘密 `CTL_PUBLISH_TOKEN` 必填。默认推送托管仓库并发布到ctl，同时保留 GitHub Release 包入口。清空地址变量不再切换至GHCR，旧版本发布包仍可按原方式安装。地址必须与ctl实例及项目登记的镜像仓库一致；连接/鉴权失败不自动切换。项目自己的流水线负责业务测试，引用审核过的实际平台 SHA。

Notes重跑发布时先用publisher读取同版本元数据并下载已登记的原始包。源码commit必须一致；已有ctl版本跳过镜像重建、重新推送和stable变更。GitHub阶段验证已有同名资产的字节，只补上传缺失资产；发现不同内容直接拒绝。包采用固定gzip时间/文件名，同输入产生相同字节，不放宽版本不可变规则。

## 目标服务器安装

先安装支持平台模式的ctl，并确认 `ctl --version` 为1.7.0或兼容新版。安装器及self-update只获取真实已发布版本。

```bash
sudo ctl login --server https://ctl.shier.art
sudo ctl install notes --prod
sudo ctl upgrade notes --prod
sudo ctl upgrade notes --prod --version v0.3.0 --port 9000
sudo ctl status notes --prod
sudo ctl rollback notes --prod
```

登录交互读取 scoped token，不在命令行或 URL 传明文。默认保存到 `/etc/deployctl/client.json`（目录700、文件600、当前身份所有，拒绝链接）；`--client-config` 可指定其他私有位置。sudo登录和sudo部署使用同一个身份。

平台模式不传环境时使用项目默认环境；`--prod` 等于 `--env prod`。首次安装不传版本时使用环境选定目标。status/restart/rollback等本地操作只在主机唯一已安装环境时可省略环境。

一次安装只解析一次版本和配置修订。随后下载同源包、校验 checksum/digest、合并配置、拉取镜像、执行 pre、必要时回读生成文件、启动、健康检查和 post。管理台在安装期间保存的新配置不会改变这次安装。

托管 Registry 拉取使用5分钟、指定仓库的 bearer，保存到临时私有 Docker 配置；应用容器和 hooks 不获得平台 Token。ctl首次拉取后，hook中的 Docker辅助命令使用 `--pull never`。

### 配置优先级

业务变量：`config.env` → `secrets.env` → 上次本地覆盖 → 本次 `--unset-env` / `--env-var` → 本次管理台修订。安装参数：本次 `--set` → 本次管理台安装参数。pre回读后重新应用同一管理台修订。值保持字面字符串，不做shell展开。

管理台删除键后，下次 upgrade 回退到本地较低层；空值仍覆盖本地。管理台值不会保存为本地 override。每组/最终合并128键，键128字符、值4096字符、总UTF-8 64KiB。

部署默认值：项目包默认 → 管理台默认 → 显式 CLI。未指定新端口/绑定时升级保留主机当前绑定。资源覆盖保存到 `.management.json`，与实际运行配置一起进入受检快照。rollback/restart 不读取当前管理台配置。

### 兼容原安装方式

```bash
ctl install notes --env prod --release <真实发布包地址或本地路径> --sha256 <真实校验值>
```

显式 `--release` 默认不访问管理服务，环境仍必填。可加 `--with-platform-config` 获取同项目/环境/版本配置，要求包与平台登记版本完全一致。`--release` 与 `--version` 冲突；首次离线拉取仍需镜像可用。

失败恢复上次成功版本、实际快照、端口与资源。缓存中经过验证的镜像允许离线回滚；缓存缺失且Registry不可访问时明确失败。配置文件、数据库迁移、邀请等hook副作用需要项目自行处理。

安装结果上报失败不影响已完成的服务，非秘密待上报记录存于配置目录的 `receipts/`，下次 managed install/upgrade尝试补报。

## 运维、备份与恢复

```bash
sudo docker compose --project-name ctl-platform \
  --env-file /opt/ctl-platform/compose.env -f control-deploy/compose.yaml --profile database ps
```

外部数据库实例省略 `--profile database`。平台密钥与数据库连接文件属容器UID10001，目录700；宿主机root可读取。Registry只挂载公开验证证书，签名私钥仅给API。

升级平台前分别备份：PostgreSQL、`artifacts/`、`registry/`、私有`keys/`、`instance.json` / `compose.env`。本地数据库使用`pg_dump -U ctl -d ctl`导出；外部数据库沿用其备份制度。停止写入/平台容器后备份Registry和包目录，以获得一致快照。加密主密钥应单独加密保管；仅有数据库备份无法恢复配置。

恢复到新的空实例目录：恢复数据库、包与Registry目录、**同一套密钥/验证证书**及实例设置，确认UID/权限，使用同一或兼容版本镜像启动，再检查 readiness、登录、解析版本及镜像拉取。不要在已有业务数据上重新生成密钥。Registry签名证书有效期10年；轮换时同时更新API私钥与Registry验证证书，并保留可解密历史配置的主密钥。

健康接口 `/api/v1/health/ready`检查数据库。托管镜像拉取失败还需单独检查Registry与HTTPS代理；历史安装成功不代表主机在线。首版只保存最近200条查询结果，但不自动删除历史数据。
