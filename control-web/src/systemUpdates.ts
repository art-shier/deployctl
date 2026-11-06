import { APIError } from "./api";
import type { DeploymentNode } from "./remote";

export interface Updater {
  id: string;
  kind: "node" | "center";
  target_id: string;
  current_version: string;
  helper_version: string;
  protocol: number;
  platform: string;
  ready: boolean;
  blocked_reason: string;
  last_seen: string | null;
  online: boolean;
}
export interface SystemRelease {
  cli_url?: string;
  server_url?: string;
  registration_source?: string;
  registered_at?: string;
  version: string;
  published_at: string;
  cli_sha256: string;
  server_sha256: string;
}
export interface ReleaseCatalog {
  releases: SystemRelease[];
  source: "bundled" | "cache" | "remote" | "none" | "registered";
  generated_at: string | null;
  last_success_at: string | null;
  last_attempt_at: string | null;
  next_sync_at: string | null;
  stale: boolean;
  sync_error: string;
}
export const catalogErrorText = (code: string) =>
  ({
    database_error: "中心版本记录读取失败，请重试。",
    network_error: "无法连接官方清单，请检查中心服务器的网络。",
    timeout: "同步超时，可以点击立即同步清单重试。",
    rate_limited: "官方清单服务暂时限流，请稍后重试。",
    not_found: "官方清单尚不可用，请稍后重试。",
    http_error: "官方清单服务返回异常，请稍后重试。",
    invalid_manifest: "官方清单校验失败，本次数据未采用。",
    cache_write_failed: "清单缓存保存失败，请检查中心的数据目录。",
  })[code] || (code ? "清单同步失败，请稍后重试。" : "");
export interface UpdateJob {
  id: string;
  request_id: string;
  batch_id: string;
  updater_id: string;
  kind: "node" | "center";
  target_id: string;
  version: string;
  from_version: string;
  cli_sha256: string;
  server_sha256: string;
  status: string;
  stage: string;
  result_code: string;
  current_version: string;
  created_at: string;
  updated_at: string;
  parallelism: number;
  resolved_at?: string | null;
}
export interface UpdateDraft {
  updater_ids: string[];
  version: string;
  parallelism: number;
  backup_confirmed: boolean;
}
export type UpdateRequest = UpdateDraft & { request_id: string };
export interface UpdateState {
  updaters: Updater[];
  jobs: UpdateJob[];
}

function stableParts(value: string) {
  const match = /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(value);
  return match?.slice(1).map(Number);
}
export function isNewerRelease(version: string, current: string) {
  const target = stableParts(version);
  const source = stableParts(current);
  if (!target || !source || target[0] !== 1 || target[1] < 15) return false;
  for (let index = 0; index < 3; index++) {
    if (target[index] !== source[index]) return target[index] > source[index];
  }
  return false;
}
export const activeUpdate = (status: string) =>
  ["queued", "leased", "running", "needs_attention"].includes(status);
export const blockedReason = (reason: string) =>
  ({
    setup_required: "请完成本机更新服务配置",
    unsupported_platform: "主机平台不支持更新",
    unsupported_protocol: "请在本机升级更新服务协议",
    invalid_version: "当前安装版本无法识别",
    agent_unavailable: "Agent 尚未就绪",
    pending_operation: "本机存在待处理操作",
    service_unavailable: "systemd 服务不可用",
    disabled: "节点已禁用",
    pending_authorization: "节点等待分配授权",
    backup_unavailable: "数据库备份工具不可用",
  })[reason] || "更新服务尚未就绪";
export function eligibility(
  updater: Updater,
  version: string,
  jobs: UpdateJob[],
  node?: DeploymentNode,
) {
  if (node && !node.enabled) return "节点已禁用";
  if (node?.pending_authorization) return "节点等待分配授权";
  if (!updater.online) return "更新服务失联";
  if (updater.protocol !== 1) return "更新协议不兼容";
  if (!updater.ready) return blockedReason(updater.blocked_reason);
  if (
    jobs.some(
      (job) =>
        activeUpdate(job.status) &&
        !job.resolved_at &&
        (job.updater_id === updater.id ||
          job.kind === "center" ||
          updater.kind === "center"),
    )
  )
    return "存在活动更新或需要人工处理的任务";
  if (!version) return "请选择官方版本";
  if (!isNewerRelease(version, updater.current_version))
    return "需要高于当前版本的兼容版本";
  return "";
}

export const UPDATE_REQUEST_STORAGE_KEY = "ctl:system-updates:pending-request";
type RequestStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function updateRequestStorage(): RequestStorage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function restoredRequest(raw: string): UpdateRequest | null {
  if (raw.length > 16384) return null;
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const keys = Object.keys(value).sort().join(",");
  if (keys !== "backup_confirmed,parallelism,request_id,updater_ids,version")
    return null;
  if (
    typeof value.request_id !== "string" ||
    !/^[a-f0-9]{32}$/.test(value.request_id)
  )
    return null;
  if (
    !Array.isArray(value.updater_ids) ||
    value.updater_ids.length < 1 ||
    value.updater_ids.length > 50 ||
    value.updater_ids.some(
      (id: unknown) =>
        typeof id !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(id),
    ) ||
    new Set(value.updater_ids).size !== value.updater_ids.length
  )
    return null;
  if (
    typeof value.version !== "string" ||
    value.version.length > 64 ||
    !value.version.startsWith("v") ||
    !isNewerRelease(value.version, "1.14.0")
  )
    return null;
  if (
    !Number.isInteger(value.parallelism) ||
    value.parallelism < 1 ||
    value.parallelism > 5 ||
    typeof value.backup_confirmed !== "boolean"
  )
    return null;
  if (
    value.backup_confirmed &&
    (value.updater_ids.length !== 1 || value.parallelism !== 1)
  )
    return null;
  return {
    updater_ids: value.updater_ids,
    version: value.version,
    parallelism: value.parallelism,
    backup_confirmed: value.backup_confirmed,
    request_id: value.request_id,
  };
}

// Store only the frozen, non-secret request so navigation cannot create a new identity.
export class UpdateSubmission {
  private pending: UpdateRequest | null = null;
  private inFlight = false;
  constructor(private storage: RequestStorage | null = null) {
    try {
      const raw = storage?.getItem(UPDATE_REQUEST_STORAGE_KEY);
      if (raw) {
        this.pending = restoredRequest(raw);
        if (!this.pending) this.clear();
      }
    } catch {
      this.clear();
    }
  }
  get draft(): UpdateDraft | null {
    if (!this.pending) return null;
    const { request_id: _, ...draft } = this.pending;
    return structuredClone(draft);
  }
  private clear() {
    this.pending = null;
    try {
      this.storage?.removeItem(UPDATE_REQUEST_STORAGE_KEY);
    } catch {}
  }
  reconcile(jobs: UpdateJob[]) {
    const pending = this.pending;
    if (!pending || this.inFlight) return false;
    const complete = pending.updater_ids.every((id) =>
      jobs.some(
        (job) =>
          job.request_id === pending.request_id &&
          job.updater_id === id &&
          job.version === pending.version,
      ),
    );
    if (complete) this.clear();
    return complete;
  }
  get uncertain() {
    return this.pending !== null;
  }
  async submit(
    draft: UpdateDraft,
    send: (body: UpdateRequest) => Promise<UpdateJob[]>,
  ) {
    if (this.inFlight) throw new Error("正在提交更新请求。");
    if (this.pending) {
      const { request_id: _, ...previous } = this.pending;
      if (JSON.stringify(previous) !== JSON.stringify(draft))
        throw new Error("请先重试原请求并确认结果。");
    } else {
      this.pending = {
        updater_ids: [...draft.updater_ids],
        version: draft.version,
        parallelism: draft.parallelism,
        backup_confirmed: draft.backup_confirmed,
        request_id: crypto.randomUUID().replaceAll("-", ""),
      };
    }
    try {
      this.storage?.setItem(
        UPDATE_REQUEST_STORAGE_KEY,
        JSON.stringify(this.pending),
      );
    } catch {
      throw new Error(
        "浏览器无法保存待核对更新请求，请启用当前会话存储后重试。",
      );
    }
    this.inFlight = true;
    try {
      const result = await send(this.pending);
      this.clear();
      return result;
    } catch (cause) {
      if (
        cause instanceof APIError &&
        cause.status >= 400 &&
        cause.status < 500 &&
        ![401, 403, 408, 429].includes(cause.status)
      )
        this.clear();
      throw cause;
    } finally {
      this.inFlight = false;
    }
  }
}
