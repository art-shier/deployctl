import {
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
  type FormEvent,
} from "react";
import { api, send, APIError } from "./api";
import { remoteDate } from "./remote";

type Job = {
  id: string;
  host: string;
  port: number;
  user: string;
  status: string;
  stage: string;
  error_code: string;
  node_id: string;
  created_at: string;
  expires_at: string;
};
const stages: Record<string, string> = {
  connecting: "连接 SSH 与校验权限",
  checking: "检查节点环境",
  python: "安装 Python",
  docker: "安装 Docker",
  agent: "安装并登记节点",
  heartbeat: "等待节点心跳",
  complete: "安装成功 · 请在节点列表分配授权",
};
const errors: Record<string, string> = {
  ssh_unreachable: "无法连接 SSH，请检查主机、端口及中心到节点的网络。",
  invalid_target: "主机地址不允许使用回环、链路本地或多播地址。",
  host_key_changed: "SSH 主机密钥已变化，请核对服务器后重新探测。",
  ssh_authentication_failed:
    "SSH 认证失败，请检查账号密码及服务器密码登录配置。",
  root_required: "安装需要 root 权限。",
  systemd_required: "需要使用 systemd 的 Linux 主机。",
  agent_exists: "已存在节点身份或服务，请在现有节点中维护，勿重复安装。",
  cli_exists: "主机已安装 ctl，请使用现有 CLI 手动接入节点。",
  unsupported_os: "此系统不支持自动安装 Docker，请先手动准备运行环境。",
  package_conflict:
    "存在 Docker 或容器运行时软件包，请先人工检查，安装器不会卸载它们。",
  python_required: "请先准备 Python 3.10 或以上版本。",
  repository_exists: "已有 Docker 软件源，请人工完成 Docker 安装后重试。",
  docker_unavailable:
    "已有 Docker 无法使用，请检查 Docker 服务，安装器不会重启它。",
  docker_old: "已有 Docker Engine 低于 20.10，请安排升级后重试。",
  compose_old: "需要 Docker Compose 2.30 或以上，请手动安装或升级后重试。",
  heartbeat_timeout:
    "安装命令已结束，但未收到本次节点心跳。请检查节点到中心的 HTTPS 网络和 Agent 日志。",
  interrupted:
    "安装任务已超时或中心曾重启。请检查主机实际状态后再操作，不会自动重试。",
  installation_timeout: "安装超时，请检查主机实际状态及本地日志后再操作。",
  remote_install_failed:
    "远程安装未完成，请检查 sudo 权限、是否有其他安装任务以及节点本地日志。",
  installation_failed: "节点安装失败，请检查 /var/log/ctl-node-install.*.log。",
};
const describe = (value: string) =>
  errors[value] || "操作失败，请检查网络、权限及节点本地安装日志。";

function InstallForm({ submitted }: { submitted: () => void }) {
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [user, setUser] = useState("root");
  const [password, setPassword] = useState("");
  const [sudoPassword, setSudoPassword] = useState("");
  const [fingerprint, setFingerprint] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const requestID = useRef(crypto.randomUUID().replaceAll("-", ""));
  const alive = useRef(true);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);
  const target = { host: host.trim(), port: Number(port), user: user.trim() };
  const resetKey = () => {
    setFingerprint("");
    setConfirmed(false);
    setError("");
  };
  const probe = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setFingerprint("");
    setConfirmed(false);
    controller.current = new AbortController();
    try {
      const result = await api<{ fingerprint: string }>(
        "/node-installations/probe",
        { ...send("POST", target), signal: controller.current.signal },
      );
      if (alive.current) setFingerprint(result.fingerprint);
    } catch (cause) {
      if (alive.current)
        setError(
          cause instanceof Error ? describe(cause.message) : "连接失败。",
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const install = async () => {
    if (busy || !confirmed || !password || uncertain) return;
    setBusy(true);
    setError("");
    const body = {
      ...target,
      id: requestID.current,
      fingerprint,
      password,
      sudo_password: sudoPassword,
    };
    setPassword("");
    setSudoPassword("");
    try {
      await api<Job>("/node-installations", send("POST", body));
      if (alive.current) submitted();
    } catch (cause) {
      if (
        cause instanceof APIError &&
        cause.status >= 400 &&
        cause.status < 500
      ) {
        if (alive.current)
          setError(
            cause.status === 409
              ? "目标已有安装任务或同时安装数量已达上限，请查看安装记录后重试。"
              : cause.status === 400
                ? "安装参数或中心 HTTPS 配置无效，请检查输入与中心公开地址。"
                : cause.message,
          );
        return;
      }
      // Never automatically repeat a side-effecting request after a lost response.
      try {
        const jobs = await api<Job[]>("/node-installations");
        if (jobs.some((job) => job.id === requestID.current)) {
          if (alive.current) submitted();
          return;
        }
      } catch {
        /* Retain uncertainty and keep submission disabled. */
      }
      if (alive.current) {
        setUncertain(true);
        setError(
          "未能确认提交结果。请关闭窗口，在安装记录中核对；确认没有任务后再重新操作。",
        );
      }
    } finally {
      body.password = "";
      body.sudo_password = "";
      if (alive.current) setBusy(false);
    }
  };
  return (
    <form className="form-stack node-install-form" onSubmit={probe}>
      <p>
        通过 SSH 安装节点和更新服务。Ubuntu / Debian 缺少 Docker
        时自动安装；已有 Docker 会先检查，版本不足时提示处理。
      </p>
      <fieldset disabled={busy || uncertain} className="form-stack">
        <label>
          主机地址
          <input
            required
            value={host}
            autoComplete="off"
            placeholder="192.168.1.20 或主机名"
            onChange={(e) => {
              setHost(e.target.value);
              resetKey();
            }}
          />
        </label>
        <label>
          SSH 端口
          <input
            required
            type="number"
            min="1"
            max="65535"
            value={port}
            onChange={(e) => {
              setPort(e.target.value);
              resetKey();
            }}
          />
        </label>
        <label>
          登录账号
          <input
            required
            value={user}
            autoComplete="off"
            onChange={(e) => {
              setUser(e.target.value);
              resetKey();
            }}
          />
        </label>
        <button type="submit">
          {busy && !fingerprint ? "正在探测…" : "检查连接与主机密钥"}
        </button>
        {fingerprint && (
          <>
            <p className="notice node-install-key">
              请与服务器管理员提供的 SSH 指纹核对：
              <code className="code-block">{fingerprint}</code>
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              已核对主机密钥指纹
            </label>
            <label>
              登录密码
              <input
                type="password"
                required
                value={password}
                autoComplete="new-password"
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {user !== "root" && (
              <label>
                sudo 密码（留空使用登录密码）
                <input
                  type="password"
                  value={sudoPassword}
                  autoComplete="new-password"
                  onChange={(e) => setSudoPassword(e.target.value)}
                />
              </label>
            )}
            <p className="muted small">
              账号需为 root 或具备无需终端交互的 sudo
              权限。密码仅用于本次安装，不保存。节点必须能通过 HTTPS
              访问中心。安装期间关闭窗口不会中止任务。
            </p>
            <button
              className="primary"
              type="button"
              disabled={!confirmed || !password}
              onClick={() => void install()}
            >
              {busy ? "正在提交…" : "安装节点"}
            </button>
          </>
        )}
      </fieldset>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

export function NodeInstall({
  Dialog,
}: {
  Dialog: ComponentType<{
    title: string;
    close: () => void;
    children: ReactNode;
  }>;
}) {
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [revision, refresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api<Job[]>("/node-installations", {
          signal: controller.signal,
        });
        if (!controller.signal.aborted) {
          setJobs(result);
          setError("");
        }
      } catch {
        if (!controller.signal.aborted) setError("安装记录加载失败，请重试。");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [revision]);
  return (
    <section className="remote-section" aria-label="SSH 节点安装">
      <div className="actions">
        <button onClick={() => setOpen(true)}>SSH 安装节点</button>
        <span className="muted small">
          输入远程登录信息，由节点安装器准备运行环境。
        </span>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}
          <button onClick={() => refresh((v) => v + 1)}>重试</button>
        </p>
      )}
      {jobs.length > 0 && (
        <details open={jobs.some((j) => j.status === "running")}>
          <summary>最近安装记录（{jobs.length}）</summary>
          <div className="form-stack">
            {jobs.map((job) => (
              <article key={job.id} className="notice">
                <strong>
                  {job.user}@{job.host}:{job.port}
                </strong>
                <span className="muted small">
                  {" "}
                  · {remoteDate(job.created_at)}
                </span>
                <p>
                  {job.status === "failed"
                    ? `安装未完成：${describe(job.error_code)}`
                    : stages[job.stage] || "正在安装"}
                </p>
                {job.status === "running" && (
                  <p className="muted small">
                    任务将在 {remoteDate(job.expires_at)} 前结束。页面每 5
                    秒刷新。
                  </p>
                )}
                {job.node_id && (
                  <p className="muted small">节点 ID：{job.node_id}</p>
                )}
              </article>
            ))}
          </div>
        </details>
      )}
      {open && (
        <Dialog title="SSH 安装节点" close={() => setOpen(false)}>
          <InstallForm
            submitted={() => {
              setOpen(false);
              refresh((v) => v + 1);
            }}
          />
        </Dialog>
      )}
    </section>
  );
}
