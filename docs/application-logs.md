# 应用日志

ctl 项目页「日志」统一入口：运行日志仍从节点读取（已有部署日志入口保留），事件日志由业务主动上报并保存于 ctl PostgreSQL。上报规则仅影响事件日志，不影响 stdout/stderr。

## 上报规则

每项目/环境独立，初始为空（拒绝上报）。指定规则支持 device、user、model，一条规则一种类型、多个精确匹配值；任意值匹配即可。灰度规则支持 0–100（两位小数建议），以 SHA-256(project + NUL + environment + NUL + device ID) 的前四字节大端整数取模 10000，判断 bucket < percentage * 100。规则 ID 不参与哈希，因此调大比例保留原命中设备。无设备 ID 不命中灰度。多个启用规则 OR；手动启停，无期限。100% 表示全量有设备 ID 的来源，服务端如需采集也应提供稳定实例 ID 或使用指定 user/model 规则。

规则 GET/PUT 使用 version 乐观并发；冲突返回 409，先重新加载。更新记入 ctl 审计。上报与规则更新按项目环境串行化，写入时重新匹配当前规则。客户端缓存不能绕过拒收。

## API

基础路径 `/api/v1/projects/{project}/environments/{env}/logging`。沿用 Bearer token/管理台 cookie 鉴权与 CSRF 校验。owner 全部权限；publisher 可在授权项目环境读写日志和管理规则；deployer 可在授权项目环境查询权限、读写日志，但不能修改规则。路径不是授权依据，服务端检查当前凭据、项目组和环境。

- GET `/rules` → `{version,rules}`
- PUT `/rules` ← `{version,rules:[{id,name,kind,enabled,attribute?,values?,percentage}]}`
- POST `/query-permission` ← `{attributes:[{type:"device",value:"inst_123"},{type:"user",value:"usr_123"}]}`
  → `{allowed,matchedRules:[{ruleId,matchedBy}],policyVersion}`
- POST `/events` ← `{events:[...]}` → `{accepted,rejected,duplicates}`。最多 100 条、1 MiB，非法批次整体拒绝；不命中的条目计 rejected。重试保持 event_id，不重复入库。
- GET `/events?limit=100&user_id=...&since=...` → `{items,next_cursor?}`。下一页 `before=next_cursor`，按入库 ID 倒序。筛选 user_id/device_id/model/request_id/trace_id/source/level/event/app_version/q；since/until 为带时区 RFC3339，过滤发生时间。limit 1–200。

日志必填 schema_version=1、event_id、occurred_at、level（大写 DEBUG/INFO/WARN/ERROR）、source、event、message。可选 user_id/device_id/model/app_version/request_id/trace_id/data。data 必须 JSON 对象且最多 16 KiB。received_at 服务端覆盖，发生时间允许最多超前 5 分钟。正文不解析为 HTML。

日志内容保留 3 天，每项目环境最多 10,000 条；每次写入裁剪该作用域，后台每小时清理过期数据，查询立即排除过期数据。事件 ID 独立保留 3 天用于去重，内容被容量裁剪后，窗口内重试仍不会重复入库；超过 3 天不保证幂等。去重键数量随这 3 天实际接收量增长，不受内容条数上限限制。无自动导出或长期存档。

环境必须已在项目或所属项目组配置中定义，拼写错误或未配置的环境返回 404，不会创建隐藏的日志作用域。

## SDK 与业务鉴权

代码与可打包的 ESM 包在 `sdk/logging`，无需修改接入业务即可独立开发/测试。包当前未发布 npm。SDK 通过接入方提供的 transport 调业务后端；后端验证登录态、覆盖 user_id，再用服务器上的 scoped ctl 凭据访问上述固定项目环境路径。不要把 owner/publisher/deployer token 放进 App。ctl 信任持有凭据的业务后端，不声称能自行证明终端真实型号或用户身份。

业务决定采集内容；shouldReport 用于昂贵采集前判断，report 内部再次获取/检查权限。规则缓存默认 30 秒（最多 60 秒），改账号即失效，写入端立即复查。SDK 限制最多四个并发批次、每批最多 100 条、最多三次发送；不落盘离线日志，不无限积压。重试事件 ID 不变。网络错误通过 Promise 返回，由业务捕获，不应阻塞核心操作。

## CLI / Agent

```
ctl logs lifelog --source app --env production --since 30m --json
ctl logs lifelog --source app --env production --device-id inst_123
ctl logs lifelog --source app --env production --request-id req_123 --before 123
```

`--client-config` 使用已有 ctl 凭据，不需要浏览器会话。`--tail` 最大 200，`--log-origin` 过滤日志来源（android/server 等），`--level`、`--user-id`、`--model`、`--trace-id`、`--event`、`--app-version`、`--q`、`--until` 可组合。原有 `ctl logs APPLICATION` 默认 runtime，保持本地部署行为。

尚未部署 ctl，也未修改 Lifelog、注册原生桥接或发布 npm；接入方后端适配与真实上报需另行接入。
