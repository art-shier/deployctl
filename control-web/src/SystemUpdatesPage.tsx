import {
  useEffect,
  useCallback,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
import { SystemReleaseDetails } from "./SystemReleaseRegistration";
import { RefreshCw } from "lucide-react";
import { api, send } from "./api";
import { remoteDate, type DeploymentNode } from "./remote";
import {
  activeUpdate,
  catalogErrorText,
  eligibility,
  UpdateSubmission,
  updateRequestStorage,
  type UpdateDraft,
  type UpdateJob,
  type Updater,
  type ReleaseCatalog,
  type SystemRelease,
  isNewerRelease,
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
  const [tab, setTab] = useState<"catalog" | "targets" | "history">("targets");
  const [targetKind, setTargetKind] = useState<"center" | "node">("center");
  const [releaseDetails, setReleaseDetails] = useState<SystemRelease | null>(
    null,
  );
  const [state, setState] = useState<UpdateState>({ updaters: [], jobs: [] });
  const [nodes, setNodes] = useState<DeploymentNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [revision, reload] = useState(0);
  const [catalog, setCatalog] = useState<ReleaseCatalog | null>(null);
  const releases = catalog?.releases || [];
  const [catalogBusy, setCatalogBusy] = useState(true);
  const [catalogSyncing, setCatalogSyncing] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [catalogNotice, setCatalogNotice] = useState("");
  const catalogRequest = useRef<AbortController | null>(null);
  const manualCatalogRequest = useRef(false);
  const catalogSelectionLocked = useRef(false);
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
  const loadCatalog = useCallback(async (force: boolean) => {
    if (manualCatalogRequest.current) return;
    catalogRequest.current?.abort();
    const controller = new AbortController();
    catalogRequest.current = controller;
    manualCatalogRequest.current = force;
    setCatalogSyncing(force);
    setCatalogBusy(true);
    if (force) setCatalogNotice("");
    try {
      const result = await api<ReleaseCatalog>(
        force ? "/system-updates/catalog/sync" : "/system-updates/catalog",
        { ...(force ? send("POST", {}) : {}), signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setCatalog(result);
      setCatalogError("");
      if (force && !result.sync_error) setCatalogNotice("清单已同步");
      if (result.sync_error) setCatalogNotice("");
      if (!catalogSelectionLocked.current) {
        setVersion((current) =>
          result.releases.some((item) => item.version === current)
            ? current
            : "",
        );
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setCatalogError(errorText(cause));
        setCatalogNotice("");
      }
    } finally {
      if (!controller.signal.aborted) {
        setCatalogBusy(false);
        setCatalogSyncing(false);
        manualCatalogRequest.current = false;
        catalogRequest.current = null;
      }
    }
  }, []);
  useEffect(() => {
    void loadCatalog(false);
    const timer = setInterval(() => {
      if (!catalogRequest.current) void loadCatalog(false);
    }, 30000);
    return () => {
      clearInterval(timer);
      catalogRequest.current?.abort();
    };
  }, [loadCatalog]);
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
  catalogSelectionLocked.current = locked || !!review;
  const catalogUnavailable = !releases.some((item) => item.version === version);
  const catalogFailure =
    catalogError || catalogErrorText(catalog?.sync_error || "");
  const reason = (target: Target) => {
    const release = releases.find((item) => item.version === version);
    const base = `https://github.com/art-shier/deployctl/releases/download/${version}/`;
    const custom =
      release &&
      ((release.cli_url && release.cli_url !== base + "deployctl.pyz") ||
        (release.server_url &&
          release.server_url !== base + `ctl-platform-${version}.tar.gz`));
    if (
      custom &&
      target.updater &&
      isNewerRelease("v1.17.0", target.updater.helper_version)
    )
      return "自定义下载地址需要先将更新器升级至 1.17.0";
    return target.updater
      ? eligibility(target.updater, version, state.jobs, target.node)
      : "尚未配置更新服务";
  };
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
        setTab("targets");
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
  const latestJob = (kind: string, id: string) =>
    state.jobs
      .filter((j) => j.kind === kind && j.target_id === id)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
  const lastSuccess = (kind: string, id: string) => {
    const job = state.jobs
      .filter(
        (j) =>
          j.kind === kind && j.target_id === id && j.status === "succeeded",
      )
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
    return job ? remoteDate(job.updated_at) : "暂无成功更新记录";
  };
  const progress = (kind: string, id: string) => {
    const job = latestJob(kind, id);
    return job
      ? `${statusName(job.status)} · ${stageName(job.stage)}`
      : "暂无更新任务";
  };
  const taskActions = (kind: string, id: string) => {
    const job = latestJob(kind, id);
    return job?.status === "queued" ? (
      <button disabled={locked} onClick={() => void jobAction(job, "cancel")}>
        取消排队
      </button>
    ) : job?.status === "needs_attention" && !job.resolved_at ? (
      <button disabled={locked} onClick={() => void jobAction(job, "resolve")}>
        确认已恢复
      </button>
    ) : null;
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
      {releaseDetails && (
        <SystemReleaseDetails
          release={releaseDetails}
          Dialog={Dialog}
          close={() => setReleaseDetails(null)}
        />
      )}
      <div className="tabs" role="tablist" aria-label="系统更新流程">
        {(
          [
            ["targets", "更新"],
            ["catalog", "可用版本"],
            ["history", "更新历史"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            id={`update-tab-${key}`}
            role="tab"
            aria-selected={tab === key}
            aria-controls={`update-panel-${key}`}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {label}
            {key === "history" &&
              state.jobs.filter((j) => activeUpdate(j.status) && !j.resolved_at)
                .length > 0 && (
                <span className="badge">
                  {
                    state.jobs.filter(
                      (j) => activeUpdate(j.status) && !j.resolved_at,
                    ).length
                  }{" "}
                  进行中
                </span>
              )}
          </button>
        ))}
      </div>
      <div className="system-update-workspace">
        <section
          hidden={tab !== "catalog"}
          role="tabpanel"
          id="update-panel-catalog"
          aria-labelledby="update-tab-catalog"
          className="panel form-stack update-catalog-panel"
        >
          <div className="section-heading">
            <h2>可用版本</h2>
            <button
              disabled={catalogBusy || locked || !!review}
              onClick={() => void loadCatalog(false)}
            >
              {catalog?.source === "registered"
                ? "刷新版本"
                : catalogSyncing
                  ? "正在同步…"
                  : "立即同步清单"}
            </button>
          </div>
          <p className="muted small">
            查看已发布的兼容版本和详情。执行更新请前往“更新”页签。
          </p>
          {catalogBusy && !catalog && <p role="status">正在加载版本清单…</p>}
          {catalog && (
            <div className="muted small">
              <p>
                清单来源：
                <span>
                  {
                    {
                      bundled: "镜像内置清单",
                      cache: "本地缓存",
                      remote: "在线同步",
                      none: "暂无清单",
                      registered: "中心版本记录",
                    }[catalog.source]
                  }
                </span>
                {catalog.generated_at && (
                  <> · 清单生成于 {remoteDate(catalog.generated_at)}</>
                )}
              </p>
              {catalog.source !== "registered" && (
                <p>
                  {catalog.last_success_at ? (
                    <>上次成功同步：{remoteDate(catalog.last_success_at)}</>
                  ) : (
                    <span>尚未完成在线同步</span>
                  )}
                  {catalog.last_attempt_at && (
                    <> · 上次尝试：{remoteDate(catalog.last_attempt_at)}</>
                  )}
                </p>
              )}
              {catalog.stale && releases.length > 0 && (
                <p>
                  当前使用已有清单，可能不是最新版本。中心会自动重试，也可立即同步。
                </p>
              )}
            </div>
          )}
          {releases.length > 0 && (
            <div
              className="table-wrap update-catalog-list"
              tabIndex={0}
              role="region"
              aria-label="可用版本列表，可滚动"
            >
              <table aria-label="登记版本列表">
                <thead>
                  <tr>
                    <th>版本</th>
                    <th>发布时间</th>
                    <th>来源</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {releases.map((release) => (
                    <tr
                      key={release.version}
                      className={
                        version === release.version ? "selected-release" : ""
                      }
                    >
                      <td>{release.version}</td>
                      <td>{remoteDate(release.published_at)}</td>
                      <td>
                        {{
                          pipeline: "流水线登记",
                          manual: "手动登记",
                          imported: "历史清单导入",
                        }[release.registration_source || "imported"] ||
                          "已登记"}
                      </td>
                      <td>
                        <div className="actions">
                          <button onClick={() => setReleaseDetails(release)}>
                            详情
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {catalogNotice && (
            <p className="notice" role="status">
              {catalogNotice}
            </p>
          )}
          {catalogFailure && (
            <div className="notice error" role="alert">
              {catalogFailure}
              {releases.length > 0 && (
                <p>已保留现有版本清单，可继续选择已知版本。</p>
              )}
            </div>
          )}
          {!catalogBusy && !releases.length && (
            <p className="notice">
              {catalog?.source === "registered"
                ? "暂无可用的兼容版本，请登记版本或等待发布流水线登记。"
                : "暂无可用的兼容版本，请点击「立即同步清单」。"}
            </p>
          )}
        </section>
        <section
          hidden={tab !== "targets"}
          role="tabpanel"
          id="update-panel-targets"
          aria-labelledby="update-tab-targets"
          className="panel form-stack update-targets-panel"
        >
          <div className="tabs" role="tablist" aria-label="更新类型">
            {(
              [
                ["center", "管理中心"],
                ["node", "Agent 节点"],
              ] as const
            ).map(([kind, label]) => (
              <button
                key={kind}
                role="tab"
                aria-selected={targetKind === kind}
                disabled={locked}
                onClick={() => setTargetKind(kind)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="section-heading">
            <h2>
              {targetKind === "center" ? "管理中心更新" : "Agent 节点更新"}
            </h2>
            <span className="muted small">每 5 秒刷新</span>
          </div>
          <div className="form-two">
            <label>
              目标版本
              <select
                aria-label="目标版本"
                value={version}
                disabled={locked || catalogBusy || !releases.length}
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
            {targetKind === "node" && (
              <label>
                节点批次并发
                <select
                  aria-label="节点批次并发"
                  value={parallelism}
                  disabled={locked}
                  onChange={(event) =>
                    setParallelism(Number(event.target.value))
                  }
                >
                  {[1, 2, 3, 4, 5].map((value) => (
                    <option key={value} value={value}>
                      {value === 1
                        ? "1 · 逐个更新（默认）"
                        : value + " · 并行更新"}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <p className="muted small">
            一次最多选择 50
            个节点。中心更新单独执行，维护期间暂停领取新的业务部署任务，已有任务会先完成。
          </p>
          {targetKind === "center" ? (
            <div className="update-target">
              <dl className="update-details">
                <dt>当前版本</dt>
                <dd>{centerUpdater?.current_version || "尚未上报"}</dd>
                <dt>连接状态</dt>
                <dd>{centerUpdater?.online ? "在线" : "尚未连接"}</dd>
                <dt>上次更新时间</dt>
                <dd>{lastSuccess("center", "center")}</dd>
                <dt>更新进度</dt>
                <dd>{progress("center", "center")}</dd>
              </dl>
              <p className="muted small">{reason(targets[0])}</p>
              <div className="actions">
                <button onClick={() => setSetup(targets[0])}>更新说明</button>
                <button
                  className="primary"
                  disabled={
                    locked ||
                    !centerUpdater ||
                    !!reason(targets[0]) ||
                    catalogBusy ||
                    catalogUnavailable ||
                    !!loadError
                  }
                  onClick={() => startReview("center")}
                >
                  更新管理中心
                </button>
              </div>
              {taskActions("center", "center")}
            </div>
          ) : (
            <>
              <div className="table-wrap">
                <table aria-label="Agent 节点更新">
                  <thead>
                    <tr>
                      <th>选择</th>
                      <th>设备名称 / IP</th>
                      <th>当前版本</th>
                      <th>上次更新时间</th>
                      <th>更新进度</th>
                      <th>状态 / 说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    {targets
                      .filter((t) => t.kind === "node")
                      .map((target) => {
                        const updater = target.updater;
                        const why = reason(target);
                        return (
                          <tr key={target.target_id}>
                            <td>
                              <input
                                type="checkbox"
                                aria-label={`选择 ${target.name}`}
                                checked={
                                  !!updater && selected.includes(updater.id)
                                }
                                disabled={
                                  !updater ||
                                  locked ||
                                  (!selected.includes(updater.id) &&
                                    (!!why ||
                                      catalogBusy ||
                                      catalogUnavailable ||
                                      !!loadError ||
                                      selected.length >= 50))
                                }
                                onChange={(e) => {
                                  if (updater)
                                    setSelected((current) =>
                                      e.target.checked
                                        ? [...current, updater.id]
                                        : current.filter(
                                            (id) => id !== updater.id,
                                          ),
                                    );
                                }}
                              />
                            </td>
                            <td>
                              <strong>{target.name}</strong>
                              <div className="muted small">IP：未上报</div>
                            </td>
                            <td>
                              {updater?.current_version ||
                                target.node?.agent_version ||
                                "尚未上报"}
                            </td>
                            <td>{lastSuccess("node", target.target_id)}</td>
                            <td>
                              {progress("node", target.target_id)}
                              {taskActions("node", target.target_id)}
                            </td>
                            <td>
                              <span className="badge">
                                {updater?.online ? "在线" : "未连接"}
                              </span>
                              <div className="muted small">
                                {why || "可更新"}
                              </div>
                              <button onClick={() => setSetup(target)}>
                                更新说明
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
              {!nodes.length && (
                <p className="update-empty muted">
                  暂无 Agent 节点，请先在“部署节点”接入设备。
                </p>
              )}
              <div className="actions">
                <span className="muted small">
                  已选 {selected.length} 个节点
                </span>
                <button
                  className="primary"
                  disabled={
                    locked ||
                    !validSelection ||
                    catalogBusy ||
                    catalogUnavailable ||
                    !!loadError
                  }
                  onClick={() => startReview("node")}
                >
                  更新选中节点
                </button>
              </div>
            </>
          )}
        </section>
        <section
          hidden={tab !== "history"}
          role="tabpanel"
          id="update-panel-history"
          aria-labelledby="update-tab-history"
          className="panel form-stack update-history-panel"
        >
          <h2>更新历史</h2>
          {!state.jobs.length ? (
            <div className="update-empty">
              <h3>暂无更新任务</h3>
              <p className="muted">
                选择版本和更新目标，确认提交后在这里查看进度。
              </p>
            </div>
          ) : (
            <div className="table-wrap">
              <table aria-label="系统更新进度与历史">
                <thead>
                  <tr>
                    <th>更新目标</th>
                    <th>版本</th>
                    <th>状态 / 阶段</th>
                    <th>更新时间</th>
                  </tr>
                </thead>
                <tbody>
                  {state.jobs.map((job) => (
                    <tr key={job.id}>
                      <td>
                        <strong>
                          {job.kind === "center"
                            ? "管理中心"
                            : nodes.find((n) => n.id === job.target_id)?.name ||
                              job.target_id}
                        </strong>
                        <details className="update-job-details">
                          <summary>任务详情</summary>
                          <span className="muted small">
                            任务 {job.id}
                            <br />
                            并发 {job.parallelism}
                            {job.current_version && (
                              <>
                                <br />
                                已观测安装版本 {job.current_version}
                              </>
                            )}
                          </span>
                        </details>
                      </td>
                      <td>
                        <code>
                          {job.from_version || "未知"} → {job.version}
                        </code>
                      </td>
                      <td>
                        <span className="badge">{statusName(job.status)}</span>
                        <div className="muted small">
                          {stageName(job.stage)}
                          {job.result_code &&
                            ` · ${resultName(job.result_code)}`}
                        </div>
                        {job.status === "needs_attention" && (
                          <p className="small">
                            {job.resolved_at
                              ? "已确认恢复，保留历史结果。"
                              : "请先修复目标主机，再确认恢复。"}
                          </p>
                        )}
                      </td>
                      <td>{remoteDate(job.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
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
