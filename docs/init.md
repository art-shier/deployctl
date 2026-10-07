# deployctl init：初始化项目接入

init在业务仓库生成标准部署描述和GitHub工作流，不执行构建、推送、发布或服务器安装。当前CLI为v1.3.0。

## 常用命令

在已有Dockerfile的项目根目录运行：

```bash
deployctl init project-a \
  --platform-repository art-shier/deployctl \
  --platform-ref v1.3.0 --private-platform \
  --port 8080 --health-path /health/ready
```

平台仓库/引用填写实际可用的值。init只检查格式，不连接GitHub验证仓库存在。工具版本和远程平台引用独立；不能仅凭本地CLI版本判断远程标签已经存在。

生成：

```text
deploy/deployment.yaml
.github/workflows/release.yml
```

镜像版本相关的release.yaml仍由构建流水线生成。

## 预览与额外配置

- --dry-run：打印包含完整YAML的JSON预览，不创建文件/目录。
- --private-platform：为私有平台工作流映射PLATFORM_READ_TOKEN；项目需配置该secret并满足平台Actions访问范围要求。
- --required-config DATABASE_URL：声明一个必需变量，可重复；不填秘密值，默认没有必需项。
- --test-command：将项目实际测试命令接入公共构建流程。
- --with-deploy-workflow：额外生成.github/workflows/deploy.yml，使用手动workflow_dispatch；需要另行配置SSH和环境审批。
- --directory：指定业务目录，默认当前目录。
- --dockerfile、--context：指定实际已存在的构建文件和上下文。
- --deployment-file、--workflow-file、--deploy-workflow-file：自定义输出路径；工作流必须位于.github/workflows。

默认容器端口8080、就绪路径/health/ready、绑定127.0.0.1。应从业务代码核实后填写参数，不能仅因生成成功就认为服务可用。

## 已有文件和验证

生成前检查全部输出路径。任何目标文件已存在就报冲突并停止，不覆盖，也不提供强制覆盖选项。可以手动合并已有文件，或选择不冲突的输出路径。写文件过程中发生错误会清理本次创建的YAML文件；已有文件和Dockerfile保持原样。

```bash
deployctl validate deploy/deployment.yaml
deployctl init --help
```

init验证字段和路径，生成可交给公共工作流的输入；还需要项目测试、GitHub构建、实际健康接口和目标服务器验证。
