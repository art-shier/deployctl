import {
  remoteDate,
  type DeploymentNode,
  type ResourceCapacity,
} from "./remote";
const bytes = (value: number) => `${(value / 1024 ** 3).toFixed(1)} GiB`;
function capacity(value: ResourceCapacity | null) {
  if (!value) return "未采集";
  const used = value.total_bytes - value.available_bytes;
  return `${bytes(used)} / ${bytes(value.total_bytes)}（${((100 * used) / value.total_bytes).toFixed(1)}%）`;
}
export function NodeResources({
  node,
  unavailable = false,
}: {
  node: DeploymentNode;
  unavailable?: boolean;
}) {
  if (!node.metrics)
    return <span className="muted">尚未采集（需新版 Agent）</span>;
  const metrics = node.metrics;
  const age = Date.now() - Date.parse(node.metrics_at || "");
  const fresh =
    !unavailable &&
    node.online &&
    Number.isFinite(age) &&
    age >= -5000 &&
    age <= 90000;
  return (
    <div className="node-resources">
      <strong>{fresh ? "最近采集" : "历史数据 · 当前状态未知"}</strong>
      <span>
        CPU{" "}
        {metrics.cpu_percent == null
          ? "采样中 / 未采集"
          : `${metrics.cpu_percent.toFixed(1)}%`}{" "}
        · {metrics.cpu_cores || "未知"} 核
      </span>
      <span>内存 {capacity(metrics.memory)}</span>
      <span>磁盘 {capacity(metrics.disk)}</span>
      {metrics.disk && (
        <small className="muted">
          剩余 {bytes(metrics.disk.available_bytes)} · 部署目录{" "}
          {metrics.disk.path}
        </small>
      )}
      <small className="muted">{remoteDate(node.metrics_at || null)}</small>
    </div>
  );
}
