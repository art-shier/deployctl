import { useEffect, useRef, useState, type FormEvent } from "react";
import { RefreshCw } from "lucide-react";
import { api, send, type Project, type Release } from "./api";
import {
  DeploymentSubmission,
  activeTask,
  cancellableTask,
  findInstance,
  healthLabel,
  nodeLabel,
  remoteDate,
  stageLabel,
  taskLabel,
  type DeploymentDraft,
  type DeploymentNode,
  type DeploymentTask,
  type Instance,
} from "./remote";

const message = (cause: unknown) =>
  cause instanceof Error ? cause.message : "操作失败，请重试。";
const actionNames: Record<string, string> = {
  install: "安装",
  upgrade: "升级",
  rollback: "回滚",
};

export function ProjectDeployments({ project }: { project: Project }) {
  const base = `/projects/${project.slug}`;
  const [environments, setEnvironments] = useState<string[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [nodes, setNodes] = useState<DeploymentNode[]>([]);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [tasks, setTasks] = useState<DeploymentTask[]>([]);
  const [draft, setDraft] = useState<DeploymentDraft>({
    node_id: "",
    environment: project.default_environment,
    action: "install",
    version: "",
    host_port: "",
    bind_address: "",
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [optionsError, setOptionsError] = useState("");
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [revision, reload] = useState(0);
  const submission = useRef(new DeploymentSubmission());
  const mounted = useRef(true);
  const locked = useRef(false);
  locked.current = busy || uncertain;
  useEffect(() => {
    mounted.current = true;
    const guard = (event: Event) => {
      if (
        locked.current &&
        !confirm(
          "部署请求的结果尚未确认。离开后请先查看任务列表，避免重复创建。继续离开？",
        )
      )
        event.preventDefault();
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (locked.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const click = (event: MouseEvent) => {
      const link = (event.target as Element).closest<HTMLAnchorElement>(
        'a[href^="#"]',
      );
      if (link && link.hash !== location.hash && !event.defaultPrevented)
        guard(event);
    };
    window.addEventListener("ctl-before-navigate", guard);
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", click, true);
    return () => {
      mounted.current = false;
      window.removeEventListener("ctl-before-navigate", guard);
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", click, true);
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const get = <T,>(path: string) =>
      api<T>(path, { signal: controller.signal });
    setOptionsLoading(true);
    setOptionsError("");
    Promise.all([
      get<string[]>(`${base}/environments`),
      get<Release[]>(`${base}/releases`),
    ])
      .then(([environments, releases]) => {
        if (!controller.signal.aborted) {
          setEnvironments(environments);
          setReleases(releases);
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setOptionsError(message(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setOptionsLoading(false);
      });
    return () => controller.abort();
  }, [base, revision]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    const refresh = async () => {
      try {
        const [nodes, instances, tasks] = await Promise.all([
          api<DeploymentNode[]>(
            `${base}/nodes?environment=${encodeURIComponent(draft.environment)}`,
            { signal: controller.signal },
          ),
          api<Instance[]>(`${base}/instances`, { signal: controller.signal }),
          api<DeploymentTask[]>(`${base}/tasks`, { signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) {
          setNodes(nodes);
          setInstances(instances);
          setTasks(tasks);
          setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(message(cause));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(refresh, 5000);
        }
      }
    };
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [base, draft.environment, revision]);
  const environmentTasks = tasks.filter(
    (task) => task.environment === draft.environment,
  );
  const node = nodes.find((node) => node.id === draft.node_id);
  const instance = findInstance(instances, draft.node_id, draft.environment);
  const active = tasks.some(
    (task) =>
      task.node_id === draft.node_id &&
      task.environment === draft.environment &&
      activeTask(task.status),
  );
  const needsRollback =
    instance?.pending_transaction && draft.action !== "rollback";
  const update = (changes: Partial<DeploymentDraft>) => {
    setDraft((value) => ({ ...value, ...changes }));
    setActionError("");
    setNotice("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      const task = await submission.current.submit(draft, (body) =>
        api<DeploymentTask>(`${base}/tasks`, send("POST", body)),
      );
      if (mounted.current) {
        setNotice(`任务已创建 · ${task.id}`);
        reload((value) => value + 1);
      }
    } catch (cause) {
      if (mounted.current)
        setActionError(
          submission.current.uncertain
            ? "任务创建结果尚未确认。请重试原任务请求，系统会使用同一请求标识确认结果。"
            : message(cause),
        );
    } finally {
      if (mounted.current) {
        setUncertain(submission.current.uncertain);
        setBusy(false);
      }
    }
  };
  const cancel = async (task: DeploymentTask) => {
    if (cancelling) return;
    setCancelling(task.id);
    setActionError("");
    try {
      await api(`${base}/tasks/${task.id}/cancel`, send("POST", {}));
      if (mounted.current) reload((value) => value + 1);
    } catch (cause) {
      if (mounted.current) setActionError(message(cause));
    } finally {
      if (mounted.current) setCancelling("");
    }
  };
  const nodeName = (id: string) =>
    nodes.find((node) => node.id === id)?.name || id;
  return (
    <div className="remote-page">
      <div className="section-heading">
        <div>
          <h2>远程部署</h2>
          <p className="muted small">
            每次选择一个节点。同一项目环境可在多台节点部署，版本与任务分别记录。
          </p>
        </div>
        <button disabled={busy} onClick={() => reload((value) => value + 1)}>
          <RefreshCw size={16} />
          刷新部署
        </button>
      </div>
      {(error || optionsError) && (
        <div className="notice error" role="alert">
          {error || optionsError}
          <button onClick={() => reload((value) => value + 1)}>重试加载</button>
        </div>
      )}
      <form className="panel form-stack" onSubmit={submit}>
        <h3>创建部署任务</h3>
        <fieldset disabled={busy || uncertain} className="remote-fields">
          <div className="form-two">
            <label>
              部署环境
              <select
                value={draft.environment}
                onChange={(event) =>
                  update({ environment: event.target.value, node_id: "" })
                }
              >
                {[...new Set([project.default_environment, ...environments])]
                  .filter(Boolean)
                  .map((environment) => (
                    <option key={environment} value={environment}>
                      {environment}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              目标节点
              <select
                required
                value={draft.node_id}
                onChange={(event) => update({ node_id: event.target.value })}
              >
                <option value="">请选择节点</option>
                {nodes.map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.name} · {nodeLabel(node)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {!loading && !nodes.length && (
            <p className="muted">
              当前环境没有可用节点，请在部署节点中配置授权并启用节点。
            </p>
          )}
          {node && !node.online && (
            <p className="notice">
              节点失联，任务将排队等待连接。任务创建后 24 小时内可开始执行。
            </p>
          )}
          <div className="form-two">
            <label>
              操作
              <select
                aria-label="操作"
                value={draft.action}
                onChange={(event) =>
                  update({
                    action: event.target.value as DeploymentDraft["action"],
                  })
                }
              >
                <option value="install">安装</option>
                <option value="upgrade">升级</option>
                <option value="rollback">回滚</option>
              </select>
            </label>
            <label>
              部署版本
              <select
                value={draft.version}
                disabled={draft.action === "rollback"}
                onChange={(event) => update({ version: event.target.value })}
              >
                <option value="">使用环境目标版本</option>
                {releases
                  .filter((release) => release.status === "published")
                  .map((release) => (
                    <option key={release.id} value={release.version}>
                      {release.version}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <div className="form-two">
            <label>
              主机端口
              <input
                type="number"
                min={1}
                max={65535}
                step={1}
                disabled={draft.action === "rollback"}
                value={draft.host_port}
                onChange={(event) => update({ host_port: event.target.value })}
                placeholder="使用环境默认端口"
              />
            </label>
            <label>
              绑定地址
              <input
                disabled={draft.action === "rollback"}
                value={draft.bind_address}
                onChange={(event) =>
                  update({ bind_address: event.target.value })
                }
                placeholder="使用环境默认地址（默认 127.0.0.1）"
              />
            </label>
          </div>
        </fieldset>
        <p className="muted small">
          任务创建时冻结版本与配置。回滚使用节点本地受检缓存及原绑定设置。有未完成事务时请先回滚恢复。
        </p>
        <p className="muted small">
          多节点流量需手动配置 Nginx、HAProxy
          或云负载均衡。跨机访问须显式设置受防火墙保护的可达绑定地址，验证实例健康后再加入流量。
        </p>
        {needsRollback && (
          <p className="notice">所选实例有未完成事务，请选择回滚恢复。</p>
        )}
        {active && !uncertain && (
          <p className="notice">
            所选节点的此环境已有未完成任务，请等待任务结束。
          </p>
        )}
        {actionError && (
          <p className="notice error" role="alert">
            {actionError}
          </p>
        )}
        {notice && (
          <p className="notice" role="status">
            {notice}
          </p>
        )}
        <div className="actions">
          <button
            className="primary"
            disabled={
              busy ||
              (!uncertain &&
                (loading ||
                  optionsLoading ||
                  !!error ||
                  !!optionsError ||
                  !node ||
                  active ||
                  !!needsRollback))
            }
          >
            {busy ? "正在提交…" : uncertain ? "重试原任务请求" : "创建部署任务"}
          </button>
        </div>
      </form>
      <section className="remote-section">
        <h3>已部署实例</h3>
        <p className="muted small">
          业务健康取自最后一次观测。离线期间显示历史状态。
        </p>
        {loading && (
          <p className="loading" role="status">
            正在刷新部署状态…
          </p>
        )}
        {instances.filter(
          (instance) => instance.environment === draft.environment,
        ).length ? (
          <div className="table-wrap">
            <table aria-label="已部署实例">
              <thead>
                <tr>
                  <th>节点 / 环境</th>
                  <th>实际版本</th>
                  <th>健康观测</th>
                  <th>绑定</th>
                  <th>事务</th>
                </tr>
              </thead>
              <tbody>
                {instances
                  .filter(
                    (instance) => instance.environment === draft.environment,
                  )
                  .map((instance) => (
                    <tr key={instance.id}>
                      <td>
                        {nodeName(instance.node_id)}
                        <small className="block muted">
                          {instance.environment} ·{" "}
                          {nodes.find((node) => node.id === instance.node_id)
                            ? nodeLabel(
                                nodes.find(
                                  (node) => node.id === instance.node_id,
                                )!,
                              )
                            : "节点不在当前可用范围"}
                        </small>
                      </td>
                      <td>
                        <code>{instance.actual_version || "未安装"}</code>
                      </td>
                      <td>
                        {healthLabel(
                          instance,
                          !!nodes.find((node) => node.id === instance.node_id)
                            ?.online,
                        )}
                        <small className="block muted">
                          {remoteDate(instance.observed_at)}
                        </small>
                      </td>
                      <td>
                        <code>
                          {instance.binding
                            ? `${instance.binding.bind_address}:${instance.binding.host_port}`
                            : "—"}
                        </code>
                      </td>
                      <td>
                        {instance.pending_transaction
                          ? "待回滚恢复"
                          : "无待处理事务"}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ) : (
          !loading && <p className="empty">此环境尚无部署实例。</p>
        )}
      </section>
      <section className="remote-section">
        <h3>部署任务</h3>
        <p className="muted small">
          最近 200
          条任务。排队与待开始任务可取消，执行中的任务由节点完成并上报。
        </p>
        {environmentTasks.length ? (
          <div className="table-wrap">
            <table aria-label="部署任务">
              <thead>
                <tr>
                  <th>节点 / 环境</th>
                  <th>操作 / 版本</th>
                  <th>状态 / 阶段</th>
                  <th>结果</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {environmentTasks.map((task) => (
                  <tr key={task.id}>
                    <td>
                      {nodeName(task.node_id)}
                      <small className="block muted">{task.environment}</small>
                      <code className="block muted">{task.id}</code>
                    </td>
                    <td>
                      {actionNames[task.action] || task.action}
                      <small className="block">{task.version || "—"}</small>
                    </td>
                    <td>
                      <span className="badge">
                        {taskLabel(
                          task.status,
                          nodes.find((node) => node.id === task.node_id)
                            ?.online,
                        )}
                      </span>
                      <small className="block muted">
                        {stageLabel(task.stage)}
                      </small>
                    </td>
                    <td>
                      {task.result?.code
                        ? taskLabel(task.result.code, true)
                        : "—"}
                    </td>
                    <td>{remoteDate(task.updated_at)}</td>
                    <td>
                      {cancellableTask(task.status) && (
                        <button
                          disabled={!!cancelling}
                          aria-label={`取消任务 ${task.id}`}
                          onClick={() => void cancel(task)}
                        >
                          {cancelling === task.id ? "正在取消…" : "取消任务"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          !loading && <p className="empty">暂无部署任务。</p>
        )}
      </section>
    </div>
  );
}
