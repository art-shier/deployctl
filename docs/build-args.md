# 构建参数

CLI/平台v1.4.0支持项目在接入后自行填写`deployment.yaml`的`build.args`，以及通过发布工作流`build-args`逐次覆盖。init继续生成基础配置。

完整配置、Dockerfile脚本接收方式、覆盖顺序与校验步骤见 [构建参数指南](../skills/team-deploy/references/build-args.md)。这份指南同时随独立Agent skill分发。
