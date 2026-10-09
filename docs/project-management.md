# 项目管理与配置 CLI（v1.11.0）

此能力需要同时升级CLI与管理服务。已有publisher凭据继续有效，按原有项目/项目组授权获得管理能力；非空`environments`限制配置环境，留空允许授权项目的全部环境。授权某个项目不等于授权其所在组；排除项目始终拒绝。

publisher新建项目使用平台默认的`RegistryHost/项目标识`托管路径，不能选择其他项目仓库或外部路径；修改项目时不能改变镜像仓库绑定。owner可以指定或修改仓库（包括共享仓库），避免通过项目资料编辑扩大镜像权限。

| 操作 | publisher | deployer | owner |
|---|---|---|---|
| 发布版本、推送镜像 | 授权项目 | 不允许 | 全部 |
| 部署解析、拉取镜像 | 保留镜像拉取，不允许部署解析 | 原有项目与环境范围 | 全部 |
| 新建项目 | 显式授权组内，初始环境须符合范围 | 不允许 | 全部 |
| 修改项目资料 | 授权项目 | 不允许 | 全部 |
| 项目配置读写 | 授权项目与配置环境 | 不新增管理读写；原部署读取不变 | 全部 |
| 移组、删除项目 | 不允许 | 不允许 | 全部 |
| 新增/修改/删除组、读写组配置、管理凭据 | 不允许；可列出显式授权组 | 不允许 | 全部 |

登录后使用同一凭据文件，无需再次传server地址：

```bash
ctl project list
ctl project show notes
ctl group list
ctl project create another-app --group apps --name "Another app" --default-env prod
ctl project update notes --name "Notes" --description "Personal notes"
ctl project-config list notes
ctl project-config get notes --prod
# 明确要求才输出秘密，包括本项目的继承配置，请勿重定向到公开日志：
ctl project-config get notes --prod --reveal
ctl project-config set notes DB_HOST db.example.com --prod --public
ctl project-config set notes DB_PASSWORD --prod --secret --value-file /private/db-password.txt
ctl project-config set notes ADMIN_EMAIL admin@example.com --prod --kind install --public
ctl project-config unset notes ADMIN_EMAIL --prod --kind install
ctl project-config apply notes --prod --file /private/notes-prod-patch.json
```

项目配置省略环境时使用项目默认环境。组配置必须指定`--env`或`--prod`。`--kind runtime`为启动变量（默认），`--kind install`为安装参数。秘密文件与JSON文件需当前用户所有、私有权限（Linux建议600），拒绝链接及超过64 KiB的输入；值文件只去掉一个末尾换行，不剥离值中的空格。敏感值优先通过文件输入，避免进入命令历史。写入输出始终遮盖秘密。

apply JSON与API的变更格式一致，支持`set/keep/remove`，可同时修改端口/资源默认值和部署目标：

```json
{
  "expected_revision": 3,
  "runtime_env": [{"key": "APP_ORIGIN", "operation": "set", "value": "https://notes.shier.art", "secret": false}],
  "install_params": [{"key": "ADMIN_EMAIL", "operation": "keep"}],
  "deployment_defaults": {"host_port": 8084, "memory_limit": "256m", "cpus": 1},
  "target_version": "stable"
}
```

写入读取当前修订后提交；apply内可固定`expected_revision`，也可显式使用`--expected-revision`。409不自动覆盖或重试，重新读取后对比修改。GET默认隐藏秘密；`?reveal=true`才返回本项目及继承数组中的明文，读取记入审计且响应禁止缓存。组配置的读取、修改与秘密查看仅owner可用。

owner还可以管理项目组、移动项目或删除：

```bash
ctl group create apps --name "Applications"
ctl group show apps
ctl group update apps --description "Shared application settings"
ctl group-config list apps
ctl group-config get apps --prod
ctl group-config set apps DB_HOST db.example.com --prod --public
ctl group-config apply apps --prod --file /private/apps-prod-patch.json
ctl project move notes --group apps
ctl project delete retired-app --confirm retired-app
ctl group delete retired-group --confirm retired-group
```

删除需要输入完全一致的标识；管理台同样输入标识确认。服务端归档对象，活动列表、配置、发布、部署解析与新Registry授权停止访问，原配置、镜像、制品与审计保留。已签发的Registry bearer沿用最长5分钟期限。已安装容器继续运行，删除不会远程停止或卸载服务。归档标识不可复用；凭据编辑仍可看到并移除已归档范围。仅空组可删除，已归档项目不阻止删除，`default`永久保留。API成功删除204，不存在404，非空/default组或复用标识409。

已有用户安装的CLI用原用户更新，服务端操作需要root：

```bash
ctl self-update --version v1.11.0
sudo "$(command -v ctl)" server-upgrade --version v1.11.0
```

升级保留现有实例端口、数据库、Token与配置。仍先按[管理服务备份说明](control-plane.md)备份；升级后刷新管理台。
