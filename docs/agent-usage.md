# 使用 Team Deploy skill

skill负责让agent理解并使用本工具包；`deployctl`负责校验和运行，GitHub Actions负责构建和交付。skill不会凭空获得GitHub、SSH或生产权限。

## 文件与安装

源文件在 `skills/team-deploy/`：

```text
team-deploy/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── onboarding.md
│   └── operations.md
└── assets/
    ├── deployctl.pyz
    ├── deployctl.pyz.sha256
    ├── install.sh
    └── templates/
```

可以从平台Release下载team-deploy-skill-v1.4.0.zip，单独分发整个team-deploy目录，不能只复制SKILL.md而遗漏资源。当前自包含CLI为v1.4.0，支持init和私有平台接入；发布包协议保持schema_version: 1。工具升级时需要同步CLI、模板和指南。

按Codex当前[官方技能文档](https://learn.chatgpt.com/docs/build-skills)，仓库级技能可放在项目 `.agents/skills/team-deploy/`，用户级技能可放在 `~/.agents/skills/team-deploy/`。本地已有环境也可能配置CODEX_HOME/skills；沿用实际加载目录，避免同名skill同时安装到多个目录。

安装方法：将分发包中的顶层team-deploy文件夹复制到所选skills目录，保留其结构。已有同名skill时先检查再更新，不盲目嵌套复制。Codex未显示新skill时刷新或重启。其他兼容Agent Skills的agent使用各自的加载位置。

## 项目接入示例

在业务项目中给agent这个请求：

```text
使用 $team-deploy 帮当前项目接入团队标准交付。
平台仓库：art-shier/deployctl（私有，init添加--private-platform）
平台引用：v1.4.0或实际审核过的SHA
本次只修改并验证本地接入文件，不推送、不发布、不操作服务器。
```

agent会读取项目代码，复用Dockerfile，填写deployment.yaml，添加或合并公共构建工作流，并使用随附CLI做真实配置校验。未提供平台仓库/引用时，它应列出信息缺口并继续可完成的本地准备。

新接入优先通过init预览并生成文件。已有文件不覆盖，按需合并或选择其他工作流文件名。平台引用沿用用户给定的实际值，不自动改成CLI版本号。

技能也覆盖ctl/deployctl命令缺失和工具自身更新：先检查PATH与版本，需要时在线或离线安装；CLI>=1.3.0的受管理命令使用self-update，旧版通过重新执行安装器更新。细节见 [CLI安装与升级](../skills/team-deploy/references/cli-lifecycle.md)，工具更新与业务upgrade分开处理。

## 服务操作示例

已经明确目标且准备执行部署时：

```text
使用 $team-deploy 把project-a在指定Linux主机的production环境升级到v1.1.0。
发布包：实际已发布的URL
SHA256：流水线提供的实际摘要
使用已有部署身份与配置，执行后验证就绪、当前版本及transaction状态。
```

只有诊断时：

```text
使用 $team-deploy 诊断project-a的production升级失败。
先根据状态和日志给出原因及恢复步骤，不执行恢复或重启。
```

skill遵守任务已有授权：明确请求的操作可以执行，单纯接入或诊断不会自动发布/重启生产服务。密钥通过服务器或凭证管理注入，不填到仓库、产物或对话中。

## 验证边界

技能格式校验、随附CLI和模板执行，以及隔离fixture中的agent行为测试在本地进行。测试使用示例仓库名，没有真实GitHub发布或生产操作。真实部署仍需完成工具包的Linux Docker和目标服务器验证。
