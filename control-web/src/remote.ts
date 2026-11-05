import { APIError } from "./api";

export interface DeploymentNode {
  id: string;
  name: string;
  enabled: boolean;
  groups: string[];
  projects: string[];
  excluded_projects: string[];
  environments: string[];
  created_at: string;
  last_seen: string | null;
  agent_version: string;
  platform: string;
  docker_available: boolean;
  online: boolean;
}
export interface Instance {
  id: string;
  node_id: string;
  project: string;
  environment: string;
  actual_version: string;
  health: string;
  observed_at: string | null;
  binding: { host_port: number; bind_address: string } | null;
  pending_transaction: boolean;
}
export interface DeploymentTask {
  id: string;
  instance_id: string;
  node_id: string;
  project: string;
  environment: string;
  action: string;
  actor: string;
  request_id: string;
  status: string;
  stage: string;
  version: string;
  created_at: string;
  updated_at: string;
  result: {
    code: string;
    stage: string;
    observation?: Omit<Instance, "node_id" | "observed_at">;
  } | null;
}
export interface DeploymentDraft {
  node_id: string;
  environment: string;
  action: "install" | "upgrade" | "rollback";
  version: string;
  host_port: string;
  bind_address: string;
}
export interface TaskPayload {
  node_id: string;
  environment: string;
  action: DeploymentDraft["action"];
  version?: string;
  host_port?: number;
  bind_address?: string;
}
export function deploymentPayload(draft: DeploymentDraft): TaskPayload {
  if (!draft.node_id) throw new Error("请选择目标节点。");
  if (!draft.environment) throw new Error("请选择环境。");
  const body: TaskPayload = {
    node_id: draft.node_id,
    environment: draft.environment,
    action: draft.action,
  };
  if (draft.action === "rollback") return body;
  if (draft.version) body.version = draft.version;
  if (draft.host_port.trim()) {
    if (
      !/^\d+$/.test(draft.host_port.trim()) ||
      Number(draft.host_port) < 1 ||
      Number(draft.host_port) > 65535
    )
      throw new Error("主机端口须为 1 到 65535 的整数。");
    body.host_port = Number(draft.host_port);
  }
  if (draft.bind_address.trim()) body.bind_address = draft.bind_address.trim();
  return body;
}

// Keep the exact payload until an ambiguous response is acknowledged by retry.
export class DeploymentSubmission {
  private pending: (TaskPayload & { request_id: string }) | null = null;
  private inFlight = false;
  get uncertain() {
    return this.pending !== null;
  }
  async submit<T>(
    draft: DeploymentDraft,
    send: (body: TaskPayload & { request_id: string }) => Promise<T>,
  ): Promise<T> {
    if (this.inFlight) throw new Error("请求正在提交，请等待。");
    const body = deploymentPayload(draft);
    if (this.pending) {
      const { request_id: _, ...previous } = this.pending;
      if (JSON.stringify(previous) !== JSON.stringify(body))
        throw new Error("请先重试原任务请求，确认执行结果。");
    } else {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      this.pending = {
        ...body,
        request_id: Array.from(bytes, (value) =>
          value.toString(16).padStart(2, "0"),
        ).join(""),
      };
    }
    this.inFlight = true;
    try {
      const result = await send(this.pending);
      this.pending = null;
      return result;
    } catch (cause) {
      if (
        cause instanceof APIError &&
        cause.status >= 400 &&
        cause.status !== 408 &&
        cause.status < 500
      )
        this.pending = null;
      throw cause;
    } finally {
      this.inFlight = false;
    }
  }
}
export function findInstance<
  T extends { node_id: string; environment: string },
>(instances: T[], nodeId: string, environment: string) {
  return instances.find(
    (instance) =>
      instance.node_id === nodeId && instance.environment === environment,
  );
}
export const nodeLabel = (node: { enabled: boolean; online: boolean }) =>
  !node.enabled ? "已禁用" : node.online ? "在线" : "失联";
const healthNames: Record<string, string> = {
  unknown: "未知",
  healthy: "健康",
  unhealthy: "异常",
  stopped: "已停止",
  not_installed: "未安装",
};
export function healthLabel(
  instance: { health: string; observed_at: string | null },
  online: boolean,
  now = Date.now(),
) {
  if (!instance.observed_at) return "尚未观测";
  const fresh = online && now - Date.parse(instance.observed_at) <= 90000;
  return `${fresh ? "最近观测" : "上次观测"} · ${healthNames[instance.health] || "未知"}`;
}
export const activeTask = (status: string) =>
  ["queued", "leased", "running"].includes(status);
export const cancellableTask = (status: string) =>
  ["queued", "leased"].includes(status);
const taskNames: Record<string, string> = {
  queued: "排队中",
  leased: "待开始",
  running: "执行中",
  succeeded: "成功",
  failed: "失败",
  needs_attention: "需要处理",
  cancelled: "已取消",
  expired: "已过期",
};
export function taskLabel(status: string, online: boolean | undefined) {
  if (status === "running" && online === undefined)
    return "执行中 · 连接状态未知";
  if (status === "running" && !online) return "执行中 · 节点失联";
  return taskNames[status] || status;
}
export const stageLabel = (stage: string) =>
  ({
    accepted: "已接受",
    preparing: "准备",
    downloading: "下载",
    pulling: "拉取镜像",
    deploying: "部署",
    verifying: "验证",
    rollback: "回滚",
    completed: "已完成",
    execution_failed: "执行失败",
    interrupted: "执行中断",
  })[stage] || stage;
export const remoteDate = (value: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "尚未上报";
