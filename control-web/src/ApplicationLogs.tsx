import {
  useState,
  useEffect,
  useRef,
  type ComponentType,
  type ReactNode,
} from "react";
import { api, send, type Project } from "./api";
import { LogViewer, type LogTarget } from "./LogViewer";
import type { Instance } from "./remote";
import {
  parseRule,
  logQuery,
  type LogPolicy,
  type RuleDraft,
  type Decision,
  type AppLog,
  type LogPage,
} from "./logEventsModel";
import "./applicationLogs.css";
type DialogType = ComponentType<{
  title: string;
  close: () => void;
  children: ReactNode;
}>;
const msg = (e: unknown) =>
  e instanceof Error ? e.message : "操作失败，请重试。";
export function ApplicationLogs({
  project,
  Dialog,
}: {
  project: Project;
  Dialog: DialogType;
}) {
  const [env, setEnv] = useState(project.default_environment),
    [envs, setEnvs] = useState<string[]>([]),
    [error, setError] = useState("");
  useEffect(() => {
    const c = new AbortController();
    api<string[]>(`/projects/${project.slug}/environments`, {
      signal: c.signal,
    })
      .then((v) => {
        if (!c.signal.aborted) setEnvs(v);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(msg(e));
      });
    return () => c.abort();
  }, [project.slug]);
  return (
    <>
      <div className="section-heading">
        <h2>日志</h2>
        <label>
          环境{" "}
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            {[...new Set([project.default_environment, ...envs])].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
      </div>
      {error && <p role="alert">{error}</p>}
      <LogWorkspace
        key={project.slug + ":" + env}
        project={project}
        env={env}
        Dialog={Dialog}
      />
    </>
  );
}
function LogWorkspace({
  project,
  env,
  Dialog,
}: {
  project: Project;
  env: string;
  Dialog: DialogType;
}) {
  const [tab, setTab] = useState("events");
  return (
    <div className="app-logs">
      <div className="tabs" role="tablist" aria-label="日志类型">
        {[
          ["runtime", "运行日志"],
          ["events", "事件日志"],
          ["rules", "上报规则"],
          ["integration", "接入说明"],
        ].map(([v, label]) => (
          <button
            key={v}
            role="tab"
            aria-selected={tab === v}
            onClick={() => setTab(v)}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "runtime" ? (
          <RuntimeLogs project={project} env={env} Dialog={Dialog} />
        ) : tab === "events" ? (
          <Events project={project.slug} env={env} Dialog={Dialog} />
        ) : tab === "rules" ? (
          <Rules project={project.slug} env={env} Dialog={Dialog} />
        ) : (
          <Integration project={project.slug} env={env} />
        )}
      </div>
    </div>
  );
}
function RuntimeLogs({
  project,
  env,
  Dialog,
}: {
  project: Project;
  env: string;
  Dialog: DialogType;
}) {
  const [items, setItems] = useState<Instance[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(true),
    [target, setTarget] = useState<LogTarget | null>(null);
  useEffect(() => {
    let c = new AbortController();
    api<Instance[]>(`/projects/${project.slug}/instances`, { signal: c.signal })
      .then((v) => {
        if (!c.signal.aborted) setItems(v.filter((i) => i.environment === env));
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(msg(e));
      })
      .finally(() => {
        if (!c.signal.aborted) setBusy(false);
      });
    return () => c.abort();
  }, [project.slug, env]);
  return (
    <>
      <p className="muted">
        按需读取节点容器日志，不受上报规则影响。部署过程日志仍可从部署任务查看。
      </p>
      {error && <p role="alert">{error}</p>}
      {busy ? (
        <p role="status">正在读取实例…</p>
      ) : items.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>节点</th>
                <th>版本</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id}>
                  <td>{i.node_id}</td>
                  <td>{i.actual_version}</td>
                  <td>
                    <button
                      onClick={() =>
                        setTarget({
                          instance: i.id,
                          node: i.node_id,
                          environment: env,
                          services: Object.keys(i.stack?.services || {}),
                        })
                      }
                    >
                      查看运行日志
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>该环境暂无已部署实例。</p>
      )}
      {target && (
        <LogViewer
          project={project.slug}
          target={target}
          Dialog={Dialog}
          close={() => setTarget(null)}
        />
      )}
    </>
  );
}
function Events({
  project,
  env,
  Dialog,
}: {
  project: string;
  env: string;
  Dialog: DialogType;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({}),
    [filters, setFilters] = useState<Record<string, string>>({}),
    [cursor, setCursor] = useState<number>(),
    [page, setPage] = useState<LogPage>({ items: [] }),
    [revision, setRevision] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [detail, setDetail] = useState<AppLog | null>(null);
  useEffect(() => {
    const c = new AbortController();
    setBusy(true);
    setError("");
    setPage({ items: [] });
    api<LogPage>(
      `/projects/${project}/environments/${env}/logging/events?${logQuery(filters, cursor)}`,
      { signal: c.signal },
    )
      .then((v) => {
        if (!c.signal.aborted) setPage(v);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(msg(e));
      })
      .finally(() => {
        if (!c.signal.aborted) setBusy(false);
      });
    return () => c.abort();
  }, [project, env, filters, cursor, revision]);
  const labels: Record<string, string> = {
    q: "关键词",
    user_id: "用户 ID",
    device_id: "设备 ID",
    model: "设备型号",
    request_id: "Request ID",
    trace_id: "Trace ID",
    event: "事件类型",
    app_version: "应用版本",
    source: "来源",
  };
  return (
    <>
      <form
        className="app-log-filters"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          setCursor(undefined);
          setFilters({ ...draft });
          setRevision((x) => x + 1);
        }}
      >
        {Object.entries(labels).map(([k, l]) => (
          <label key={k}>
            {l}
            <input
              value={draft[k] || ""}
              maxLength={256}
              onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
            />
          </label>
        ))}
        <label>
          级别
          <select
            value={draft.level || ""}
            onChange={(e) => setDraft({ ...draft, level: e.target.value })}
          >
            <option value="">全部级别</option>
            {["DEBUG", "INFO", "WARN", "ERROR"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        {["since", "until"].map((k, i) => (
          <label key={k}>
            {i ? "结束时间" : "开始时间"}
            <input
              type="datetime-local"
              onChange={(e) => {
                const v = e.target.value;
                setDraft({ ...draft, [k]: v ? new Date(v).toISOString() : "" });
              }}
            />
          </label>
        ))}
        <button className="primary" disabled={busy}>
          查询
        </button>
        <button
          type="reset"
          onClick={() => {
            setDraft({});
            setFilters({});
            setCursor(undefined);
          }}
        >
          清空条件
        </button>
      </form>
      <p className="muted small">
        按接收顺序倒序；保留 3 天，每项目环境最多 10,000 条。业务字段以原始 JSON
        展示。
      </p>
      {error && (
        <p role="alert" className="notice">
          {error}
        </p>
      )}
      {busy ? (
        <p role="status">正在查询日志…</p>
      ) : page.items.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>发生时间</th>
                <th>级别 / 来源</th>
                <th>事件 / 内容</th>
                <th>用户 / 设备</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((x) => (
                <tr key={x.event_id}>
                  <td>{new Date(x.occurred_at).toLocaleString()}</td>
                  <td>
                    {x.level}
                    <br />
                    {x.source}
                  </td>
                  <td>
                    {x.event}
                    <div className="app-log-message">{x.message}</div>
                  </td>
                  <td>
                    {x.user_id || "—"}
                    <br />
                    {x.device_id || "—"}
                  </td>
                  <td>
                    <button onClick={() => setDetail(x)}>详情</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !error && <p>暂无匹配日志。请检查筛选条件、业务接入和上报规则。</p>
      )}
      <div className="app-log-actions">
        <button disabled={busy || !cursor} onClick={() => setCursor(undefined)}>
          返回最新
        </button>
        <button
          disabled={busy || !page.next_cursor}
          onClick={() => setCursor(page.next_cursor)}
        >
          下一页
        </button>
        <button disabled={busy} onClick={() => setRevision((x) => x + 1)}>
          刷新
        </button>
      </div>
      {detail && (
        <Dialog title="事件日志详情" close={() => setDetail(null)}>
          <pre className="app-log-json">{JSON.stringify(detail, null, 2)}</pre>
          {detail.request_id && (
            <button
              onClick={() => {
                const f = { request_id: detail.request_id! };
                setDraft(f);
                setFilters(f);
                setCursor(undefined);
                setDetail(null);
              }}
            >
              查询相同 Request ID 的事件
            </button>
          )}
          <p className="muted small">
            服务端输出相同 Request ID 后，可在运行日志中用该 ID 搜索关联记录。
          </p>
        </Dialog>
      )}
    </>
  );
}
function Rules({
  project,
  env,
  Dialog,
}: {
  project: string;
  env: string;
  Dialog: DialogType;
}) {
  const base = `/projects/${project}/environments/${env}/logging`,
    [policy, setPolicy] = useState<LogPolicy | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [revision, setRevision] = useState(0),
    [draft, setDraft] = useState<RuleDraft | null>(null),
    [formError, setFormError] = useState(""),
    [remove, setRemove] = useState<string | null>(null),
    [attrs, setAttrs] = useState({ device: "", user: "", model: "" }),
    [decision, setDecision] = useState<Decision | null>(null),
    [checking, setChecking] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    const c = new AbortController();
    setError("");
    setPolicy(null);
    api<LogPolicy>(base + "/rules", { signal: c.signal })
      .then((v) => {
        if (!c.signal.aborted) setPolicy(v);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(msg(e));
      });
    return () => c.abort();
  }, [base, revision]);
  async function save(rules: LogPolicy["rules"]) {
    if (!policy) return;
    setBusy(true);
    setError("");
    const c = new AbortController();
    pending.current?.abort();
    pending.current = c;
    try {
      const p = await api<LogPolicy>(base + "/rules", {
        ...send("PUT", { version: policy.version, rules }),
        signal: c.signal,
      });
      if (!c.signal.aborted) {
        setPolicy(p);
        setDraft(null);
        setRemove(null);
        setDecision(null);
      }
    } catch (e) {
      if (!c.signal.aborted) setError(msg(e));
    } finally {
      if (!c.signal.aborted) setBusy(false);
    }
  }
  async function check() {
    setChecking(true);
    setError("");
    setDecision(null);
    const c = new AbortController();
    pending.current?.abort();
    pending.current = c;
    try {
      const d = await api<Decision>(base + "/query-permission", {
        ...send("POST", {
          attributes: Object.entries(attrs)
            .filter(([, v]) => v.trim())
            .map(([type, value]) => ({ type, value: value.trim() })),
        }),
        signal: c.signal,
      });
      if (!c.signal.aborted) setDecision(d);
    } catch (e) {
      if (!c.signal.aborted) setError(msg(e));
    } finally {
      if (!c.signal.aborted) setChecking(false);
    }
  }
  const edit = (id?: string) => {
    const r = policy?.rules.find((x) => x.id === id);
    setFormError("");
    setDraft({
      id: r?.id || crypto.randomUUID(),
      name: r?.name || "",
      kind: r?.kind || "specified",
      enabled: r?.enabled ?? true,
      attribute: r?.attribute || "device",
      valuesText: r?.values?.join("\n") || "",
      percentage: r?.percentage || 0,
    });
  };
  return (
    <>
      <div className="section-heading">
        <div>
          <h3>上报规则</h3>
          <p className="muted small">
            任意启用规则命中即可上报；无匹配时不上报。手动启停，无有效期限。
          </p>
        </div>
        <button disabled={!policy || busy || checking} onClick={() => edit()}>
          添加规则
        </button>
      </div>
      {error && (
        <div role="alert" className="notice">
          {error}{" "}
          <button
            disabled={busy || checking}
            onClick={() => {
              setDraft(null);
              setRemove(null);
              setRevision((x) => x + 1);
            }}
          >
            重新加载规则
          </button>
        </div>
      )}
      {!policy && !error && <p role="status">正在加载规则…</p>}
      {policy && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>名称</th>
                  <th>类型 / 匹配范围</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {policy.rules.map((r) => (
                  <tr key={r.id}>
                    <td>{r.name}</td>
                    <td>
                      {r.kind === "percentage"
                        ? `灰度上报 · ${r.percentage}%`
                        : {
                            device: "设备 ID",
                            user: "用户 ID",
                            model: "设备型号",
                          }[r.attribute!]}
                      <div className="app-log-message">
                        {r.values?.join("、")}
                      </div>
                    </td>
                    <td>{r.enabled ? "已启用" : "已停用"}</td>
                    <td>
                      <div className="app-log-actions">
                        <button
                          disabled={busy || checking}
                          onClick={() =>
                            save(
                              policy.rules.map((x) =>
                                x.id === r.id
                                  ? { ...x, enabled: !x.enabled }
                                  : x,
                              ),
                            )
                          }
                        >
                          {r.enabled ? "停用" : "启用"}
                        </button>
                        <button
                          disabled={busy || checking}
                          onClick={() => edit(r.id)}
                        >
                          编辑
                        </button>
                        <button
                          disabled={busy || checking}
                          onClick={() => setRemove(r.id)}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!policy.rules.length && (
              <p>暂无规则。添加指定或灰度规则后，匹配对象才能上报。</p>
            )}
          </div>
          <section className="app-log-test">
            <h3>测试上报权限</h3>
            <div className="app-log-filters">
              {Object.entries({
                device: "设备 ID",
                user: "用户 ID",
                model: "设备型号",
              }).map(([k, l]) => (
                <label key={k}>
                  {l}
                  <input
                    value={attrs[k as keyof typeof attrs]}
                    disabled={checking}
                    maxLength={256}
                    onChange={(e) => {
                      setAttrs({ ...attrs, [k]: e.target.value });
                      setDecision(null);
                    }}
                  />
                </label>
              ))}
              <button
                disabled={
                  busy ||
                  checking ||
                  !Object.values(attrs).some((v) => v.trim())
                }
                onClick={check}
              >
                {checking ? "正在查询…" : "查询上报权限"}
              </button>
            </div>
            {decision && (
              <div role="status">
                <strong>{decision.allowed ? "允许上报" : "无需上报"}</strong>
                <p className="muted">
                  策略版本 {decision.policyVersion} ·{" "}
                  {decision.matchedRules.length
                    ? decision.matchedRules
                        .map(
                          (m) =>
                            policy.rules.find((r) => r.id === m.ruleId)?.name ||
                            m.ruleId,
                        )
                        .join("、")
                    : "未命中启用规则"}
                </p>
              </div>
            )}
          </section>
        </>
      )}
      {draft && (
        <Dialog
          title="上报规则"
          close={() => {
            if (!busy) setDraft(null);
          }}
        >
          <form
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              try {
                const r = parseRule(draft);
                save(
                  policy!.rules.some((x) => x.id === r.id)
                    ? policy!.rules.map((x) => (x.id === r.id ? r : x))
                    : [...policy!.rules, r],
                );
              } catch (e) {
                setFormError(msg(e));
              }
            }}
          >
            <label>
              规则名称
              <input
                value={draft.name}
                maxLength={160}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label>
              上报方式
              <select
                value={draft.kind}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    kind: e.target.value as RuleDraft["kind"],
                  })
                }
              >
                <option value="specified">指定上报</option>
                <option value="percentage">灰度上报</option>
              </select>
            </label>
            {draft.kind === "specified" ? (
              <>
                <label>
                  指定对象
                  <select
                    value={draft.attribute}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        attribute: e.target.value as RuleDraft["attribute"],
                      })
                    }
                  >
                    <option value="device">设备 ID</option>
                    <option value="user">用户 ID</option>
                    <option value="model">设备型号</option>
                  </select>
                </label>
                <label>
                  匹配值（每行一个或逗号分隔）
                  <textarea
                    rows={5}
                    value={draft.valuesText}
                    onChange={(e) =>
                      setDraft({ ...draft, valuesText: e.target.value })
                    }
                  />
                </label>
              </>
            ) : (
              <label>
                灰度比例（%）
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={0.01}
                  value={draft.percentage}
                  onChange={(e) =>
                    setDraft({ ...draft, percentage: Number(e.target.value) })
                  }
                />
                <p className="muted small">
                  按设备 ID 稳定分组；100% 为全量。缺少设备 ID 不命中灰度。
                </p>
              </label>
            )}
            <label className="app-log-check">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(e) =>
                  setDraft({ ...draft, enabled: e.target.checked })
                }
              />
              启用规则
            </label>
            {(formError || error) && <p role="alert">{formError || error}</p>}
            <button className="primary" disabled={busy}>
              {busy ? "正在保存…" : "保存规则"}
            </button>
          </form>
        </Dialog>
      )}
      {remove && (
        <Dialog
          title="删除规则"
          close={() => {
            if (!busy) setRemove(null);
          }}
        >
          <p>删除后该规则不再参与上报匹配，已有日志保留。</p>
          {error && <p role="alert">{error}</p>}
          <button
            disabled={busy}
            onClick={() => save(policy!.rules.filter((r) => r.id !== remove))}
          >
            删除规则
          </button>
        </Dialog>
      )}
    </>
  );
}
function Integration({ project, env }: { project: string; env: string }) {
  return (
    <>
      <h3>SDK 与接入接口</h3>
      <p>
        业务侧决定日志内容；ctl 负责准入、存储与查询。SDK 不持有 ctl
        服务端密钥，通过业务鉴权接口转发。
      </p>
      <pre className="app-log-json">{`const decision = await logger.shouldReport();\nawait logger.report({ level: 'info', event: 'record.saved',\n  message: '记录保存成功', data: { durationMs: 343 } });\nconst context = logger.getContext();\n\nPOST /api/v1/projects/${project}/environments/${env}/logging/query-permission\n{ "attributes": [{ "type": "device", "value": "inst_123" }] }\n\nPOST /api/v1/projects/${project}/environments/${env}/logging/events\n{ "events": [{ "schema_version": 1, "event_id": "unique-id",\n  "occurred_at": "2026-11-23T10:00:00Z", "level": "INFO",\n  "source": "android", "event": "record.saved", "message": "saved",\n  "device_id": "inst_123", "data": { "durationMs": 343 } }] }\n\nctl logs ${project} --env ${env} --source app --since 30m --json`}</pre>
      <p className="muted">
        SDK 位于 ctl 仓库
        sdk/logging，提供类型定义及接入说明。用户身份须由业务登录态验证。浏览器不能可靠读取设备型号时留空，原生端可通过
        getDeviceInfo 注入。设备 ID 为随机安装 ID。
      </p>
    </>
  );
}
