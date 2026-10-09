# Agent 接入

管理服务>=1.12.0的侧栏新增「Agent 接入」。页面展示当前服务地址和对应skill/CLI版本，提供完整skill ZIP下载、SHA256、SKILL.md入口与可复制的接入说明，并可进入「组凭据」管理授权。下载资源随当前服务镜像发布，运行时不依赖GitHub；不需要MCP。

## 接入步骤

1. 从页面下载team-deploy ZIP，解压到Agent实际的技能目录。例如Codex使用`~/.codex/skills/team-deploy`，目录中直接包含SKILL.md、references和assets，不再嵌套同名目录。更新旧skill前保留用户修改；新skill在下一轮技能发现时生效。
2. 让Agent读取SKILL.md并完成当前任务。可以复制页面提供的公开说明地址；尚未安装时也能先读取该Markdown及其references，然后下载完整ZIP获得随附工具。
3. 用已安装ctl或skill内的`assets/deployctl.pyz`访问管理服务。Python>=3.10即可运行随附CLI，Windows/Linux均可管理远端项目与配置。安装/升级真正的业务容器仍需在目标Linux主机执行。
4. 由owner在管理台「组凭据」选择项目/项目组、环境和角色。Token通过交互或私有文件输入；Agent接入页面和复制的提示词不包含Token，不会自动创建或查看凭据。

```bash
# 尚未安装系统CLI时可用此入口代替下文ctl：
python /path/to/team-deploy/assets/deployctl.pyz --version

ctl config get server
# 默认服务ctl.shier.art直接login；仅在目标与当前配置不同才设置：
ctl config set server https://your-ctl.example.com
ctl login --token-file /private/ctl.token
ctl whoami
ctl project list
```

Linux Token文件需当前用户所有、权限600。Token不进入命令行值、提示词、URL或公开日志。普通用户无需sudo登录，Linux的系统服务操作才需要root。

publisher可管理授权项目资料、配置并在显式授权组内新建项目；环境限制与排除项继续生效。deployer保持原部署权限，不能修改配置或管理项目。owner拥有全部管理能力。详细命令与权限见[项目管理说明](project-management.md)。

## 自托管资源

登录后的`GET /api/v1/agent-access`返回当前镜像打包的版本、服务地址、skill/CLI路径和SHA256；不查询或返回任何业务配置或凭据。普通发布/部署凭据也可以读取这份接入元数据，权限范围不会因此变化。

公开GET/HEAD资源包括：

- `/agent/team-deploy-skill.zip`：完整skill包，与对应GitHub Release的ZIP使用同一打包函数。
- `/agent/SKILL.md`与`/agent/references/*.md`：入口及任务说明。
- `/agent/deployctl.pyz`及`/agent/assets/deployctl.pyz`：对应版本的自包含CLI。
- 上述工具的校验文件、公开安装器与模板。

这些路径只服务镜像内白名单资源，不访问数据库、私有密钥或运行配置，也不会将未知路径作为SPA返回。默认资源目录`/app/agent`；开发时先执行`python scripts/build_agent_assets.py --output /private/dev-agent-assets`并设置`CTL_AGENT_DIR`。缺失或无效资源时页面显示可重试错误，其他业务API继续运行。

## 更新

服务器升级后，页面与公开下载资源同时切换到新镜像对应版本；已下载的skill不会自动更新，需单独重新下载安装。`ctl self-update`只更新安装器管理的CLI，不覆盖skill目录。管理服务升级沿用现有数据、密钥和端口：

```bash
ctl self-update --version v1.12.1
sudo "$(command -v ctl)" server-upgrade --version v1.12.1
```
