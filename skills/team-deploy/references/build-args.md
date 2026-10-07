# 项目自行添加构建参数（CLI/平台>=1.4.0）

init生成基础接入文件，不要求填写构建参数，也没有--build-arg初始化选项。项目可以在接入后自行添加下面的配置，无需重新init。已有项目保留文件内容，按需合并；调用工作流的引用与platform-ref都需更新到同一实际可用的>=1.4.0平台版本。

## 项目默认值

在项目的deploy/deployment.yaml中增加可选build.args：

```yaml
build:
  dockerfile: Dockerfile
  context: .
  args:
    BUILD_PROFILE: production
    FEATURE_ENABLED: 'true'
    BUILD_FLAGS: '--optimize --target api'
```

键是ASCII标识符，支持大小写及下划线；所有值都是字符串，数字/布尔值需要YAML引号。最多128个参数，键最多128字符、每个值最多4096字符；不支持控制字符、换行或首尾空白。空字符串是有效值。现有配置不写args时继续使用Dockerfile默认值。

## 每次发布时覆盖

在项目release.yml调用reusable workflow的with中增加build-args，每行明确填写KEY=value。等号后的内容是原样值，不加shell引号；内部空格、逗号、双引号、美元符号与额外等号会保留。

```yaml
jobs:
  release:
    uses: art-shier/deployctl/.github/workflows/build-release.yml@v1.5.0
    with:
      platform-repository: art-shier/deployctl
      platform-ref: v1.5.0
      version: ${{ github.ref_name }}
      build-args: |
        BUILD_PROFILE=${{ vars.BUILD_PROFILE || 'production' }}
        FEATURE_ENABLED=false
    secrets:
      PLATFORM_READ_TOKEN: ${{ secrets.PLATFORM_READ_TOKEN }}
```

使用项目已有的tags或workflow_dispatch入口；手动发布输入需要项目自己声明，并把inputs值映射到build-args。deployment.yaml中的`${{ vars.* }}`不会被GitHub计算；动态表达式放在调用工作流的with中，由GitHub解析。

覆盖顺序：Dockerfile ARG默认值 → deployment.yaml build.args → 工作流build-args。未传的键保留原默认值；`KEY=`显式覆盖为空，裸KEY拒绝，重复键拒绝。GitHub变量为空时也会覆盖为空，需要默认值时像上例一样在表达式中设置fallback。平台不对参数值做shell求值。

## 交给项目构建脚本

沿用现有技术栈和Dockerfile，在实际执行脚本的构建stage声明ARG，再传给脚本：

```dockerfile
ARG BUILD_PROFILE=development
ARG FEATURE_ENABLED=false
RUN ./scripts/build.sh --profile "$BUILD_PROFILE" --feature "$FEATURE_ENABLED"
```

多阶段Dockerfile需要在使用参数的stage声明ARG；确保脚本本来支持对应参数。平台不会自动新增脚本、重写构建逻辑，或在Runner上执行一套额外构建。构建参数影响镜像内容；服务器config.env/secrets.env属于服务运行配置，install/upgrade不能重新编译已发布镜像。

build.args/build-args用于普通配置。密钥需要单独的BuildKit secrets传递；当前平台没有通用build-secrets输入，不能把Token或生产secrets当作build-args。

## 校验与交付

先确认本地CLI>=1.4.0，再执行：

```bash
ctl validate deploy/deployment.yaml
```

仅配置校验通过不能报告镜像已构建；真实流水线会检查工作流覆盖值，并把合并结果编码传给Docker构建。缺少Docker或GitHub权限时报告尚未执行对应验证，不虚构构建输出。

流水线只把这些参数交给构建；标准发布包不包含build.args，无hooks时保持schema_version: 1与minimum_deployctl_version: 1.0.0。有hooks使用协议v2。工具升级不会自动修改业务项目的workflow引用，项目需合并更新。ctl>=1.5.0的--set属于安装hook参数，和构建参数分开，见 [运行时配置与钩子](runtime-config-hooks.md)。
