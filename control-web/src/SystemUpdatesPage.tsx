import {
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
import { RefreshCw } from "lucide-react";
import { api, send } from "./api";
import { remoteDate, type DeploymentNode } from "./remote";
import {
  activeUpdate,
  blockedReason,
  eligibility,
  UpdateSubmission,
  updateRequestStorage,
  type UpdateDraft,
  type UpdateJob,
  type Updater,
  type SystemRelease,
  type UpdateState,
} from "./systemUpdates";
import "./systemUpdates.css";

type DialogType = ComponentType<{
  title: string;
  children: ReactNode;
  close: () => void;
}>;
type Target = {
  kind: "node" | "center";
  target_id: string;
  name: string;
  updater?: Updater;
  node?: DeploymentNode;
};
const errorText = (cause: unknown) =>
  cause instanceof Error ? cause.message : "操作失败，请重试。";
const statusName = (value: string) =>
  ({
    queued: "排队中",
    leased: "已领取",
    running: "执行中",
    succeeded: "成功",
    rolled_back: "已回滚",
    failed: "失败",
    needs_attention: "需要人工处理",
    cancelled: "已取消",
  })[value] || value;
const stageName = (value: string) =>
  ({
    queued: "排队",
    waiting: "等待业务任务结束",
    downloading: "下载并校验",
    backing_up: "备份",
    stopping: "停止服务",
    installing: "安装",
    restarting: "重启",
    verifying: "健康验证",
    restoring: "恢复原版本",
    completed: "已完成",
    failed: "失败",
    interrupted: "执行中断",
  })[value] || value;
const resultName = (value: string) =>
  ({
    ok: "已验证",
    download_failed: "下载或校验失败",
    backup_failed: "备份失败",
    install_failed: "安装失败",
    health_failed: "健康验证失败",
    rolled_back: "已恢复原版本",
    interrupted: "执行中断",
    manual_recovery_required: "请在主机上检查备份与待处理状态",
  })[value] || value;

function Setup({
  target,
  Dialog,
  close,
}: {
  target: Target;
  Dialog: DialogType;
  close: () => void;
}) {
  const center = target.kind === "center";
  return (
    <Dialog title={`更新服务说明 · ${target.name}`} close={close}>
      <div className="form-stack">
        <p>
          {center
            ? "更新服务随中心安装、升级自动配置。安装程序使用本机已有身份完成接入，无需另外生成凭据或登记。"
            : "节点安装时会自动安装并启动 Agent 和更新服务，只需首次接入节点的一次性凭据，无需再次登记更新器。"}
        </p>
        <p>
          更新服务在宿主机独立运行，中心或 Agent 停止升级时仍能继续执行。
          正常安装后，这里会自动显示版本和连接状态。
        </p>
        <h3>已有主机补装或修复</h3>
        <p>
          在对应的 Linux 宿主机上，使用包含此能力的官方 ctl 执行以下命令。
          {center
            ? "命令从本机中心配置读取身份。"
            : "命令复用本机节点身份，不会重新创建节点，也不再要求接入凭据。"}
        </p>
        <code className="code-block">
          {center
            ? "sudo ctl updater setup --kind center --server-home /opt/ctl-platform"
            : "sudo ctl agent install --executable /usr/local/bin/ctl"}
        </code>
        <p className="muted small">
          以上使用默认目录。自定义安装请沿用原来的路径和服务名；先运行命令的
          --help 查看参数。
          不要删除现有身份和任务日志。有正在执行或待恢复的更新时，请先处理任务。
        </p>
        <h3>检查运行状态</h3>
        <code className="code-block">
          sudo systemctl status{" "}
          {center ? "ctl-updater-center" : "ctl-agent ctl-updater-node"}
        </code>
        <p className="muted small">
          若已安装但仍未连接，请检查对应服务日志及主机到中心的网络。自定义服务名请使用实际名称。
          状态每 5 秒自动刷新。
        </p>
        <a className="inline-link" href="#agent">
          查看 CLI 安装与校验说明
        </a>
      </div>
    </Dialog>
  );
}

export function SystemUpdatesPage({ Dialog }: { Dialog: DialogType }) {
  const [state, setState] = useState<UpdateState>({ updaters: [], jobs: [] });
  const [nodes, setNodes] = useState<DeploymentNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [revision, reload] = useState(0);
  const [releases, setReleases] = useState<SystemRelease[]>([]);
  const [catalogBusy, setCatalogBusy] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [catalogRevision, reloadCatalog] = useState(0);
  const [requestStore] = useState(updateRequestStorage);
  const [submission] = useState(() => new UpdateSubmission(requestStore));
  const [version, setVersion] = useState(() => submission.draft?.version || "");
  const [selected, setSelected] = useState<string[]>(
    () => submission.draft?.updater_ids || [],
  );
  const [parallelism, setParallelism] = useState(
    () => submission.draft?.parallelism || 1,
  );
  const [setup, setSetup] = useState<Target | null>(null);
  const [review, setReview] = useState<"node" | "center" | null>(null);
  const [backup, setBackup] = useState(false);
  const [draft, setDraft] = useState<UpdateDraft | null>(
    () => submission.draft,
  );
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const mutation = useRef<AbortController | null>(null);
  useEffect(() => () => mutation.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const [next, nextNodes] = await Promise.all([
          api<UpdateState>("/system-updates", { signal: controller.signal }),
          api<DeploymentNode[]>("/nodes", { signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) {
          setState(next);
          if (submission.reconcile(next.jobs)) {
            setDraft(null);
            setReview(null);
            setSelected([]);
            setNotice("已在历史记录中确认原请求的更新任务。");
          }
          setNodes(nextNodes);
          setLoadError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setLoadError(errorText(cause));
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
  }, [revision]);
  useEffect(() => {
    const controller = new AbortController();
    setCatalogBusy(true);
    setCatalogError("");
    api<SystemRelease[]>("/system-updates/releases", {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) {
          setReleases(result);
          setVersion((current) =>
            result.some((item) => item.version === current) ? current : "",
          );
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setCatalogError(errorText(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setCatalogBusy(false);
      });
    return () => controller.abort();
  }, [catalogRevision]);
  const centerUpdater = state.updaters.find((item) => item.kind === "center");
  const targets: Target[] = [
    {
      kind: "center",
      target_id: "center",
      name: "中心",
      updater: centerUpdater,
    },
    ...nodes.map((node) => ({
      kind: "node" as const,
      target_id: node.id,
      name: node.name,
      node,
      updater: state.updaters.find(
        (item) => item.kind === "node" && item.target_id === node.id,
      ),
    })),
  ];
  const uncertain = submission.uncertain;
  const locked = busy || uncertain;
  const reason = (target: Target) =>
    target.updater
      ? eligibility(target.updater, version, state.jobs, target.node)
      : "等待更新服务自动接入";
  const chosen = targets.filter(
    (target) =>
      target.kind === "node" &&
      target.updater &&
      selected.includes(target.updater.id),
  );
  const validSelection =
    chosen.length > 0 &&
    chosen.length <= 50 &&
    chosen.length === selected.length &&
    chosen.every((target) => !reason(target));
  const startReview = (kind: "node" | "center") => {
    setActionError("");
    setNotice("");
    setBackup(false);
    setDraft({
      updater_ids: kind === "center" ? [centerUpdater!.id] : [...selected],
      version,
      parallelism: kind === "center" ? 1 : parallelism,
      backup_confirmed: false,
    });
    setReview(kind);
  };
  const submit = async () => {
    if (busy || !draft) return;
    if (!requestStore) {
      setActionError(
        "浏览器无法保存待核对更新请求，请启用当前会话存储后重试。",
      );
      return;
    }
    const controller = new AbortController();
    mutation.current = controller;
    setBusy(true);
    setActionError("");
    const body = uncertain
      ? draft
      : { ...draft, backup_confirmed: review === "center" };
    setDraft(body);
    try {
      const jobs = await submission.submit(body, (payload) =>
        api<UpdateJob[]>("/system-updates/jobs", {
          ...send("POST", payload),
          signal: controller.signal,
        }),
      );
      if (!controller.signal.aborted) {
        setState((previous) => ({
          ...previous,
          jobs: [
            ...jobs,
            ...previous.jobs.filter(
              (job) => !jobs.some((next) => next.id === job.id),
            ),
          ],
        }));
        setReview(null);
        setDraft(null);
        setSelected([]);
        setNotice("更新任务已提交，将等待相关业务任务结束后执行。");
        reload((value) => value + 1);
      }
    } catch (cause) {
      if (!controller.signal.aborted)
        setActionError(
          submission.uncertain
            ? `提交结果尚未确认。${errorText(cause)} 请使用“重试原请求”核对同一批任务。`
            : errorText(cause),
        );
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  const jobAction = async (job: UpdateJob, action: "cancel" | "resolve") => {
    if (locked) return;
    if (
      action === "resolve" &&
      !confirm(
        "请先在目标主机完成修复。中心将验证更新服务已重新上线、就绪且安装版本符合任务记录。确认已恢复？",
      )
    )
      return;
    const controller = new AbortController();
    mutation.current = controller;
    setBusy(true);
    setActionError("");
    try {
      const result = await api<UpdateJob>(
        `/system-updates/jobs/${encodeURIComponent(job.id)}/${action}`,
        { ...send("POST", {}), signal: controller.signal },
      );
      if (!controller.signal.aborted) {
        setState((previous) => ({
          ...previous,
          jobs: previous.jobs.map((item) =>
            item.id === result.id ? result : item,
          ),
        }));
        reload((value) => value + 1);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setActionError(errorText(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  const centerActive = state.jobs.some(
    (job) =>
      job.kind === "center" && activeUpdate(job.status) && !job.resolved_at,
  );
  return (
    <div className="remote-page system-updates">
      <div className="page-heading">
        <div>
          <p className="eyebrow">SYSTEM MAINTENANCE</p>
          <h1>系统更新</h1>
          <p className="muted">管理中心与节点 Agent 的官方版本更新。</p>
        </div>
        <button onClick={() => reload((value) => value + 1)} disabled={busy}>
          <RefreshCw size={16} />
          刷新状态
        </button>
      </div>
      {loading && <p role="status">正在加载更新服务…</p>}
      {loadError && (
        <div className="notice error" role="alert">
          {centerActive
            ? "中心更新期间连接中断，正在重新连接。"
            : "更新状态加载失败。"}
          {loadError}
          <button onClick={() => reload((value) => value + 1)}>重试状态</button>
          <p className="small">每 5 秒自动重连，当前显示上次状态。</p>
        </div>
      )}
      {actionError && !review && (
        <p className="notice error" role="alert">
          {actionError}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {uncertain && !review && (
        <div className="notice">
          <p>
            已恢复原请求，目标版本 {draft?.version}，共{" "}
            {draft?.updater_ids.length} 个目标，并发 {draft?.parallelism}
            。已锁定原请求的目标、版本与并发数。重试会查询或创建同一批任务。
          </p>
          <button
            className="primary"
            disabled={busy}
            onClick={() => void submit()}
          >
            重试原请求
          </button>
        </div>
      )}
      <section className="panel form-stack">
        <div className="section-heading">
          <h2>官方版本</h2>
          <button
            disabled={catalogBusy || locked}
            onClick={() => reloadCatalog((value) => value + 1)}
          >
            刷新官方版本
          </button>
        </div>
        <p className="muted small">
          版本来自 art-shier/deployctl 官方稳定发布，仅支持协议 1 的 1.15.0 至
          2.0.0 之前版本。建议先选择一个节点试点，再分批更新。
        </p>
        {catalogBusy && <p role="status">正在加载官方版本…</p>}
        {catalogError && (
          <div className="notice error" role="alert">
            {catalogError}
            <button onClick={() => reloadCatalog((value) => value + 1)}>
              重试官方版本
            </button>
          </div>
        )}
        {!catalogBusy && !catalogError && !releases.length && (
          <p className="notice">暂无兼容的官方发布版本。</p>
        )}
        <div className="form-two">
          <label>
            目标版本
            <select
              aria-label="目标版本"
              value={version}
              disabled={locked || catalogBusy || !!catalogError}
              onChange={(event) => {
                setVersion(event.target.value);
                setSelected([]);
              }}
            >
              <option value="">请选择官方版本</option>
              {releases.map((item) => (
                <option key={item.version} value={item.version}>
                  {item.version} · {remoteDate(item.published_at)}
                </option>
              ))}
            </select>
          </label>
          <label>
            节点批次并发
            <select
              aria-label="节点批次并发"
              value={parallelism}
              disabled={locked}
              onChange={(event) => setParallelism(Number(event.target.value))}
            >
              {[1, 2, 3, 4, 5].map((value) => (
                <option key={value} value={value}>
                  {value === 1 ? "1 · 逐个更新（默认）" : value + " · 并行更新"}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="muted small">
          一次最多选择 50
          个节点。中心更新单独执行，维护期间暂停领取新的业务部署任务，已有任务会先完成。
        </p>
      </section>
      <section className="panel form-stack">
        <div className="section-heading">
          <h2>更新目标</h2>
          <span className="muted small">每 5 秒刷新</span>
        </div>
        <div className="update-targets">
          {targets.map((target) => {
            const updater = target.updater;
            const disabledReason = reason(target);
            return (
              <article
                className="update-target"
                key={target.kind + target.target_id}
              >
                <div className="section-heading">
                  <h3>{target.name}</h3>
                  <span className="badge">
                    {target.kind === "center" ? "管理中心" : "节点 Agent"}
                  </span>
                </div>
                <dl className="update-details">
                  <dt>安装版本</dt>
                  <dd>
                    {updater?.current_version ||
                      target.node?.agent_version ||
                      "尚未上报"}
                  </dd>
                  <dt>连接</dt>
                  <dd>
                    {updater ? (updater.online ? "在线" : "失联") : "尚未连接"}
                  </dd>
                  <dt>就绪</dt>
                  <dd>
                    {!updater
                      ? "等待更新服务自动接入"
                      : updater.ready
                        ? "已就绪"
                        : blockedReason(updater.blocked_reason)}
                  </dd>
                </dl>
                {updater && (
                  <p className="muted small">
                    更新服务 {updater.helper_version} · 协议 {updater.protocol}{" "}
                    · {updater.platform}
                    <br />
                    最近心跳 {remoteDate(updater.last_seen)}
                  </p>
                )}
                {disabledReason && updater && (
                  <p className="muted small">{disabledReason}</p>
                )}
                <div className="actions">
                  <button onClick={() => setSetup(target)}>
                    更新说明 {target.name}
                  </button>
                  {target.kind === "node" && updater && (
                    <label className="scope-option">
                      <input
                        type="checkbox"
                        aria-label={`选择 ${target.name}`}
                        checked={selected.includes(updater.id)}
                        disabled={
                          locked ||
                          (!selected.includes(updater.id) &&
                            (!!disabledReason ||
                              catalogBusy ||
                              !!catalogError ||
                              !!loadError ||
                              selected.length >= 50))
                        }
                        onChange={(event) =>
                          setSelected((current) =>
                            event.target.checked
                              ? [...current, updater.id]
                              : current.filter((id) => id !== updater.id),
                          )
                        }
                      />
                      <span>选择更新</span>
                    </label>
                  )}
                  {target.kind === "center" && updater && (
                    <button
                      className="primary"
                      disabled={
                        locked ||
                        !!disabledReason ||
                        catalogBusy ||
                        !!catalogError ||
                        !!loadError
                      }
                      onClick={() => startReview("center")}
                    >
                      审阅中心更新
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
        <div className="actions">
          <span className="muted small">已选 {selected.length} 个节点</span>
          <button
            className="primary"
            disabled={
              locked ||
              !validSelection ||
              catalogBusy ||
              !!catalogError ||
              !!loadError
            }
            onClick={() => startReview("node")}
          >
            审阅节点更新
          </button>
        </div>
      </section>
      <section className="panel form-stack">
        <h2>进度与历史</h2>
        {!state.jobs.length ? (
          <p className="muted">暂无系统更新任务。</p>
        ) : (
          <div className="update-history">
            {state.jobs.map((job) => (
              <article className="update-target" key={job.id}>
                <div className="section-heading">
                  <h3>
                    {job.kind === "center"
                      ? "中心"
                      : nodes.find((node) => node.id === job.target_id)?.name ||
                        job.target_id}
                  </h3>
                  <span className="badge">{statusName(job.status)}</span>
                </div>
                <p>
                  <code>
                    {job.from_version || "未知"} → {job.version}
                  </code>
                </p>
                <p>
                  {stageName(job.stage)}
                  {job.result_code && ` · ${resultName(job.result_code)}`}
                </p>
                {job.current_version && (
                  <p className="muted small">
                    已观测安装版本 {job.current_version}
                  </p>
                )}
                <p className="muted small">
                  更新于 {remoteDate(job.updated_at)} · 并发 {job.parallelism}
                  <br />
                  任务 {job.id}
                </p>
                {job.status === "needs_attention" && (
                  <p className="notice">
                    {job.resolved_at
                      ? "已确认恢复，历史结果保留。"
                      : "请先在主机修复安装和待处理状态，再确认恢复以解除业务任务暂停。"}
                  </p>
                )}
                <div className="actions">
                  {job.status === "queued" && (
                    <button
                      disabled={locked}
                      onClick={() => void jobAction(job, "cancel")}
                    >
                      取消排队任务
                    </button>
                  )}
                  {job.status === "needs_attention" && !job.resolved_at && (
                    <button
                      disabled={locked}
                      onClick={() => void jobAction(job, "resolve")}
                    >
                      确认已恢复
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      {setup && (
        <Setup
          key={setup.kind + setup.target_id}
          target={setup}
          Dialog={Dialog}
          close={() => setSetup(null)}
        />
      )}
      {review && draft && (
        <Dialog
          title={review === "center" ? "确认中心更新" : "确认节点更新"}
          close={() => {
            if (!busy) setReview(null);
          }}
        >
          <div className="form-stack">
            {actionError && (
              <p className="notice error" role="alert">
                {actionError}
              </p>
            )}
            <p>
              目标版本 <strong>{draft.version}</strong> ·{" "}
              {draft.updater_ids.length} 个目标 · 并发 {draft.parallelism}
            </p>
            {review === "center" ? (
              <>
                <p>
                  管理台和 API
                  将短暂中断，页面会持续尝试重连。更新前由服务备份数据库、配置与密钥。大型镜像和物料备份由管理员另行安排。
                </p>
                <p>
                  中心更新失败时不会自动回滚数据库结构。请保留备份，在主机检查安装状态并按需人工恢复。
                </p>
                <label className="scope-option">
                  <input
                    type="checkbox"
                    checked={backup}
                    disabled={locked}
                    onChange={(event) => setBackup(event.target.checked)}
                  />
                  <span>我已安排镜像和物料备份，并接受维护中断</span>
                </label>
              </>
            ) : (
              <>
                <p>{chosen.map((target) => target.name).join("、")}</p>
                <p>
                  更新会等待已有业务任务结束，逐步下载、备份、重启和验证
                  Agent。失败时尝试恢复原版本，无法恢复的目标需要人工处理。
                </p>
              </>
            )}
            <div className="actions">
              <button disabled={busy} onClick={() => setReview(null)}>
                返回
              </button>
              {uncertain && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => void submit()}
                >
                  重试原请求
                </button>
              )}
              {!uncertain && (
                <button
                  className="primary"
                  disabled={busy || (review === "center" && !backup)}
                  onClick={() => void submit()}
                >
                  {busy
                    ? "正在提交…"
                    : review === "center"
                      ? "确认中心更新"
                      : "确认节点更新"}
                </button>
              )}
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}
