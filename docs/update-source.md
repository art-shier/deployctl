# 独立更新源与发布同步

此能力属于 CLI/Agent/中心 1.17.0 源码，尚未发布。未配置时沿用官方 GitHub 源；配置后只请求指定源，不会失败后暗中回退 GitHub。系统更新页显示当前清单 URL，已有缓存和内置清单继续可用。

## 发布端：一次配置，后续发版自动同步

准备一个对服务器可访问的 HTTPS 下载域名和兼容 S3 的对象存储，以及允许匿名拉取的 OCI 镜像仓库。下载源公开读取、写入凭据仅放 GitHub Actions Secrets。不要把密码放 URL。

在 art-shier/deployctl 的 GitHub Actions Variables 配置：

| Variable | 内容示例 |
| --- | --- |
| CTL_UPDATE_MIRROR_URL | `https://downloads.example.com/ctl`，公开目录的根地址 |
| CTL_UPDATE_S3_DESTINATION | `s3://releases/ctl`，与公开 URL 对应的对象前缀 |
| CTL_UPDATE_S3_ENDPOINT | S3 服务端点；AWS S3 可不填 |
| CTL_UPDATE_S3_REGION | 区域，默认 `us-east-1` |
| CTL_SERVER_MIRROR_REPOSITORY | `registry.example.com/team/ctl-server`，不带协议、标签或 digest |

Actions Secrets：`CTL_UPDATE_S3_ACCESS_KEY_ID`、`CTL_UPDATE_S3_SECRET_ACCESS_KEY`、`CTL_SERVER_MIRROR_USERNAME`、`CTL_SERVER_MIRROR_PASSWORD`。S3 凭据只需对应前缀的上传权限；镜像凭据需推送指定仓库的权限。下载域名不要缓存 404，`update-index.json` 应不缓存或遵循 `no-cache`。

发布流水线构建镜像，使用 `skopeo --all --preserve-digests` 同步全部架构到镜像仓库，再生成引用该仓库固定 digest 的中心安装包。同一个安装包发布到 GitHub 和独立下载源，SHA256 完全一致；不重写已发布包。配置镜像仓库后，**该次发布的中心安装包统一使用这个仓库**，包括从 GitHub 下载的安装包。

公开 Release 验证成功后自动调用 `Sync update source`：

1. 下载 Release 文件并核验清单及各安装包 SHA256。
2. 确认中心安装包指向配置的镜像仓库。
3. 上传版本目录的 CLI、中心包、安装脚本、技能包、wheel 及校验文件。
4. 从公开下载 URL 回读验证文件。
5. 最后以单个对象 PUT 发布根目录 `update-index.json`，仅包含已经同步的版本。

任何文件上传或校验失败，旧清单不变。已有版本文件不可覆盖为不同内容。同步完成后的最终回读失败会将任务标红；此时所有文件已完成验证，清单可能已经生效，可重跑确认。并发发布串行处理，拒绝让旧版本覆盖最新版本。

如果同步任务失败，在 Actions 中手动运行 **Sync update source**，输入已经发布的版本即可重试，不必重新构建或发新版本。只有复制完成的版本才出现在镜像清单中。配置之前已经发布的旧版本仍引用 GHCR，不能靠复制安装包解决镜像访问问题；需要发布首个启用镜像仓库的新版本。

## 使用端：首次安装与现有中心迁移

以下以 `<版本>` 代表实际已经同步的发布版本。先从你自己的下载站取得该版本的 `install.sh`，例如：

```bash
curl -fSLo install.sh https://downloads.example.com/ctl/<版本>/install.sh
bash ./install.sh --update-source https://downloads.example.com/ctl --user
ctl --version
```

安装器会从该源获取清单和 CLI，并记住下载源；之后 `ctl self-update` 沿用该源。若已安装在 `/root/.local/bin`，将 `--user` 换成 `--install-dir /root/.local/bin`，沿用原安装目录。

首次安装中心：

```bash
/root/.local/bin/ctl server-install --update-source https://downloads.example.com/ctl
```

已有中心升级：

```bash
/root/.local/bin/ctl server-upgrade --update-source https://downloads.example.com/ctl
```

自定义中心目录追加原来的 `--home`。该参数写入中心实例配置及 Compose 环境，之后的手动升级和控制台更新都会保留。升级后管理台每 10 分钟同步一次；失败约 1 分钟重试，“立即同步清单”可主动重试。只有更新源实际完成同步才会出现新版本。

直接部署中心容器时，设置环境变量 `CTL_UPDATE_SOURCE=https://downloads.example.com/ctl`。CLI 也接受同名环境变量作为一次调用的默认源；self-update 安装记录会保存所选源。用 `--update-source ''` 可显式恢复官方源。

## 节点更新

新版更新器与中心协商能力后，通过认证端点 `/api/v1/updater/jobs/{id}/package` 获取自己正在执行任务的更新包，不接触 GitHub、对象存储凭据或外部下载 URL。中心从配置的源下载、验证任务冻结的 SHA256 并缓存；节点再次验证 SHA256 和嵌入版本。包下载在停止服务前完成，中心自身也先取回本地安装包，再执行备份和升级。

新更新器连接旧中心时保留原直接下载模式。已经运行的旧更新器无法凭空获得代理能力：首次迁移需先用新安装器从下载源升级节点 CLI，再按现有 `ctl agent install` 流程刷新服务和更新器，保留原身份、目录与服务参数；无需重新登记身份。此后可以一直在控制台更新。

## 范围与限制

- 服务器必须能访问下载源及镜像仓库；完全隔离网络仍需要离线交付。
- 控制台查询和节点更新无需 GitHub；发布流水线仍运行于 GitHub Actions。
- 中心的 PostgreSQL、Registry 基础镜像仍使用已有 Docker Hub 来源。首次安装若 Docker Hub 也不可达，应配置 Docker 镜像加速/预加载这些镜像；本功能不宣称完整离线部署。
- 节点更新包走中心，业务项目镜像拉取仍遵循业务部署配置。
- 清单和 SHA256 依赖你信任的 HTTPS 更新源，尚未增加独立签名体系。
- 本仓库不会自动创建对象存储、域名、证书或镜像仓库。需配置真实地址及 GitHub Secrets 后才能验证生产同步链路。
