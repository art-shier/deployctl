# 项目接入与产物生成（兼容协议v1/v2）

下文镜像、Compose、--release与Docker构建/运行参数适用于Docker项目；静态项目使用[静态部署](static-deployment.md)的模板、目录和统一命令。

本文件中的相对资源路径以 skill 根目录为基准。工作目录应是业务项目，而不是 skill 目录。

## 接入项目

1. 读取业务启动代码、Dockerfile、现有 Actions 和测试配置，确定实际端口、就绪接口、启动所需环境变量和构建上下文。只有声明必需配置项的变量名称，不填生产值。
2. 平台仓库与引用优先从已有 workflow 或用户输入读取。平台尚未发布时可以完成本地接入，但应报告实际构建被平台仓库/引用阻塞；不要把示例 acme/YOUR_TEAM 当作已存在的仓库。
3. 新项目优先执行下面的init命令生成配置与发布工作流。保留项目Dockerfile；其路径及构建上下文必须已存在。就绪接口若缺失，先说明缺口，按已授权接入范围和现有框架补齐。健康检查需要直接返回HTTP 2xx。
4. init遇到已有目标文件会停止且不覆盖。此时读取已有文件，使用assets/templates按需合并，或者用--workflow-file选择另一个工作流文件；不要删除已有文件绕过冲突。调用引用、platform-ref一致，version使用项目标签，不能将本地工具版本当成已发布的平台标签。
5. 在业务目录执行 `python "$SKILL_DIR/assets/deployctl.pyz" validate deploy/deployment.yaml`。读取标准化结果和退出码。可用时再运行actionlint/项目测试；没有Docker时不能报告镜像构建已通过。

## 初始化命令（CLI >=1.1.0）

在业务目录，将变量设为用户输入或现有配置中确认的值，先预览再写入：

```bash
python "$SKILL_DIR/assets/deployctl.pyz" init "$APP" \
  --platform-repository "$PLATFORM_REPOSITORY" --platform-ref "$PLATFORM_REF" \
  --port "$CONTAINER_PORT" --health-path "$READINESS_PATH" --dry-run

python "$SKILL_DIR/assets/deployctl.pyz" init "$APP" \
  --platform-repository "$PLATFORM_REPOSITORY" --platform-ref "$PLATFORM_REF" \
  --port "$CONTAINER_PORT" --health-path "$READINESS_PATH"
```

dry-run返回包含文件路径及完整YAML的JSON，不创建目录。正常执行生成deploy/deployment.yaml和.github/workflows/release.yml，不创建release.yaml或Dockerfile，不推送、不发布。用户明确要求本地接入时可直接预览并写入，不再引入批准步骤。

必需配置变量用重复的--required-config NAME，仅列真实必需项；默认空列表。--test-command接入真实项目测试。--directory指定业务目录，--dockerfile/--context沿用实际构建路径；输出路径可通过--deployment-file/--workflow-file指定。仅用户要求自动部署时添加--with-deploy-workflow，默认不创建服务器部署入口。

实际平台art-shier/deployctl为公开仓库，无需--private-platform或PLATFORM_READ_TOKEN。用户另指定私有平台时才使用--private-platform生成只读Token映射，并核对该私有平台的reusable workflow访问规则；Token实际值不写入YAML。

最小合法描述示例（端口和健康路径必须改为项目实际值）：

```yaml
schema_version: 1
application: project-a
build:
  dockerfile: Dockerfile
  context: .
container:
  port: 8080
health:
  readiness_path: /health/ready
required_config: []
```

可选字段：container.host_port、container.bind_address；health.startup_timeout_seconds（1–600，默认120）；resources.memory_limit（例如512m）、resources.cpus（0.1–128）。完整格式以模板和 CLI validate 为准。默认绑定127.0.0.1；`APP_VERSION` 平台注入，不列入 required_config。构建路径必须在业务仓库内。

CLI/平台>=1.4.0支持可选build.args。init不要求填写参数，项目后续按需增加；需要每次发布覆盖或把参数传入现有脚本时，读取 [构建参数](build-args.md)。保持工作流引用与platform-ref为同一支持该功能的实际版本。

CLI/平台>=1.5.0支持应用JSON运行配置和可选host pre/post hooks；项目后续按需添加hooks，不需要新的init参数。读取 [运行时配置与钩子](runtime-config-hooks.md)，同步构建及可选部署workflow的调用引用与platform-ref。

## GitHub 构建接入

公共工作流路径：`.github/workflows/build-release.yml`。完整调用示例在 `assets/templates/release.yml`，不要猜测 inputs 或 secrets。

- 必填输入：platform-repository、platform-ref、version。
- 可选：deployment-file（默认deploy/deployment.yaml）、registry（默认ghcr.io）、image-name（默认业务owner/repo小写）、platforms（默认linux/amd64）、test-command、build-args（>=1.4.0，KEY=value行，覆盖项目build.args）。
- secrets：PLATFORM_READ_TOKEN（私有平台checkout）、REGISTRY_USERNAME和REGISTRY_TOKEN（非默认仓库）。避免使用 `secrets: inherit` 无差别传递生产凭证。
- caller权限：contents: write、packages: write。为项目提供真实的 test-command；未提供时只报告平台没有执行项目测试。
- 标签必须存在；只有获得发布授权后才推送标签。流水线输出 release_url、sha256、application、image。真实输出或现有 GitHub Release Assets 才是有效下载地址。

## 手工生成发布包

已有真实镜像digest、需要本地生成标准包时：

```bash
python "$SKILL_DIR/assets/deployctl.pyz" package \
  --config deploy/deployment.yaml --image "$IMAGE_WITH_DIGEST" \
  --version "$RELEASE_VERSION" --output dist
```

输出 `<application>-<version>.tar.gz` 和 `.tar.gz.sha256`。基础四文件由工具生成，不手写release.yaml或添加生产secrets。有hooks时工具校验并附声明的固定脚本成员，使用协议v2；不手工塞入其他脚本。同一版本不能覆盖，发生变更发布新版本。没有真实镜像时仅生成测试fixture并明确标识，不交付为可部署版本。

## 可选自动部署

用户请求时从 `assets/templates/deploy.yml` 合并部署入口；只接入构建时不额外创建生产部署任务。公共 deploy.yml 输入以模板为准：application、environment、release-url、sha256、platform-repository、platform-ref；mode 为 install/upgrade。

SSH_HOST、SSH_USER、SSH_PRIVATE_KEY、SSH_KNOWN_HOSTS按环境配置。主机指纹通过可信渠道核对，不能关闭 StrictHostKeyChecking。部署Runner下载校验包并SCP上传，因此服务器无需持有Release Token，但仍需要私有镜像拉取权限。内网主机需要可达的部署Runner；不擅自开放安全组。
