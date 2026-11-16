import type { Instance } from "./remote";
const healthNames: Record<string, string> = {
  healthy: "健康",
  unhealthy: "异常",
  stopped: "已停止",
  unknown: "待确认",
};
const jobNames: Record<string, string> = {
  starting: "启动中",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  unknown: "待确认",
};
export function StackDetails({
  stack,
  fresh,
}: {
  stack: NonNullable<Instance["stack"]>;
  fresh: boolean;
}) {
  return (
    <details className="stack-details">
      <summary>服务状态</summary>
      {!fresh && (
        <p className="muted small">以下为上次上报信息，当前状态待确认。</p>
      )}
      {Object.entries(stack.services).map(([name, item]) => (
        <div key={name} className="stack-service">
          <strong>{name}</strong> ·{" "}
          {fresh ? healthNames[item.health] || "待确认" : "待确认"}
          <small className="block muted">
            {item.lifecycle === "persistent" ? "持久服务" : "应用服务"}
          </small>
          <code>{item.version}</code>
          <details>
            <summary>镜像</summary>
            <code className="stack-image">{item.image}</code>
          </details>
        </div>
      ))}
      {!Object.keys(stack.services).length && (
        <p className="muted small">尚无服务状态上报。</p>
      )}
      {Object.entries(stack.jobs).map(([name, status]) => (
        <p className="small" key={name}>
          任务 {name} · {jobNames[status] || "待确认"}
        </p>
      ))}
      {stack.recovery_required && (
        <p className="notice">
          需要核对恢复状态。请先查看服务日志，再在节点执行恢复操作。
          {stack.operation_id && (
            <code className="stack-image">操作 ID：{stack.operation_id}</code>
          )}
        </p>
      )}
    </details>
  );
}
