# @deployctl/logging

ESM SDK，浏览器/支持 Web Crypto 的 WebView 与 Node >=20。`npm pack` 生成接入包；尚未发布 npm。

```js
import { createLogger } from '@deployctl/logging';
const logger = createLogger({
  project: 'lifelog', environment: 'production',
  getUserId: () => session.user?.id,
  getDeviceInfo: () => ({ model: nativeDevice.model, platform: 'android', appVersion: '0.6.16' }),
  transport: {
    queryPermission: body => authenticatedBusinessPost('/logging/query-permission', body),
    report: body => authenticatedBusinessPost('/logging/events', body),
  },
});
const decision = await logger.shouldReport();
const context = logger.getContext();
await logger.report({event:'record.saved',message:'记录保存成功',data:{durationMs:343}});
```

transport 由业务提供，绑定业务登录态。业务后端验证并覆盖用户 ID，再带服务端凭据转发 ctl。SDK 不接受或保存 ctl 密钥。平台、型号通过 getDeviceInfo 从原生桥接注入；Web 型号默认空，不猜测。型号清除首尾和连续空白；规则使用同样标准化值。

设备 ID 为每项目随机安装 ID，默认 localStorage 持久化，原生可提供同步 storage 适配器。清空存储或重装会重建 ID；存储不可用则当前 SDK 实例内稳定。用户 ID 由 getUserId 每次读取，不能用邮箱代替稳定 ID。

shouldReport 返回 `{allowed,matchedRules,policyVersion}`；report 接受一个事件或数组，返回 `{status,accepted,rejected,duplicates}`。status 为 sent/rejected/skipped/busy，sent 的批次仍可能部分 rejected，请检查计数。传输失败抛出错误。规则缓存默认30秒，最长60秒；服务端入库仍重新检查。report 不持久化日志，最多4个并发调用，失败最多3次发送。记录不得包含密码、令牌或不必要个人内容。

`node --test` 测试设备 ID、身份切换、准入、重试幂等。`index.d.ts` 提供 TypeScript 接口。
