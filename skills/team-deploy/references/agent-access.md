# 从管理台接入 Agent

ctl 服务端>=1.12.0的侧栏「Agent 接入」提供与当前服务版本一致的team-deploy skill、SHA256、随附Python CLI和可复制的接入说明。下载包包含SKILL.md、references、模板、安装器及assets/deployctl.pyz；资源来自本实例的`/agent/`，无需请求GitHub或配置MCP。

## 安装与识别

将下载ZIP中的team-deploy目录放入当前Agent的技能目录。例如Codex使用`~/.codex/skills/team-deploy`；其他Agent使用其实际支持的目录。不要再套一层team-deploy。更新前保留原目录以免丢失用户修改。skill更新独立于ctl self-update，新skill在下一轮技能发现时可用。

读取安装后的SKILL.md，选择当前任务对应的参考，不把管理台上的所有示例理解为本次执行授权。尚未安装技能时，可先读取管理台提供的公开`/agent/SKILL.md`与相对references，再下载完整ZIP以获得随附工具；远程Markdown并不表示本地已安装skill。

只需Python>=3.10即可检查随附CLI，不需要pip依赖：

```bash
python "$SKILL_DIR/assets/deployctl.pyz" --version
python "$SKILL_DIR/assets/deployctl.pyz" whoami
python "$SKILL_DIR/assets/deployctl.pyz" project list
```

可将下文中的ctl替换为上述Python入口。它可在Windows/Linux管理远端项目、配置、发布版本；真正install/upgrade服务需在用户授权的目标Linux服务器上执行。此入口不是安装器管理的系统CLI，不能用self-update覆盖skill。需要系统命令时按[CLI安装与升级](cli-lifecycle.md)安装。

## 地址与凭据

默认服务为`https://ctl.shier.art`，已有自定义地址沿用。先`ctl config get server`核对，只有目标不同且用户已授权使用该服务时执行`ctl config set server <管理台显示的服务地址>`；切换到不同服务会清除旧登录，随后执行ctl login。不要在每次登录额外传server。

由owner在管理台「组凭据」为真实项目/项目组与环境签发所需角色。Agent接入页不会查询、展示或复制Token。凭据通过交互输入或当前用户所有的私有文件（Linux权限600）传入：

```bash
ctl login --token-file /private/ctl.token
ctl whoami
ctl project list
```

Token不放进提示词、命令参数值、URL或公开日志。修改scope后后续API请求使用新权限；一次登录可以管理多个授权项目。

## 按任务使用

- publisher：发布/推送镜像、读写授权项目配置、修改项目资料、在显式授权组内新建项目。非空environments限制配置及新建初始环境，排除项优先，不能删除/移组或管理组配置/凭据。镜像仓库绑定由owner管理。
- deployer：部署指定项目/环境并拉取镜像，维持原部署配置读取能力，不能用管理命令修改配置或项目。
- owner：全部管理能力，包括项目/项目组管理与删除、组配置和凭据管理；只有任务需要相应全局权限时使用。

配置默认为遮盖秘密，只在用户确实要求读取真实值时使用get --reveal。写入使用当前expected_revision，409重新读取比较，不盲目覆盖。数据库密码等使用--value-file，变更结果不打印值。

下面仅为已存在且已授权项目的例子，替换项目及环境后按真实任务选择执行：

```bash
ctl project-config get notes --prod
ctl project-config set notes APP_ORIGIN https://notes.shier.art --prod --public
ctl project-config apply notes --prod --file /private/notes-prod.json
ctl install notes --prod
```

配置/项目管理、发布和主机部署的详细契约分别见[管理服务模式](control-plane.md)、[项目接入](onboarding.md)和[服务器操作](operations.md)。
