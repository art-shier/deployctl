import { useEffect, useState, type ComponentType, type ReactNode } from "react";
import { api, send } from "./api";
import { remoteDate } from "./remote";

export interface LogTarget {
  instance: string;
  node: string;
  environment: string;
  task?: string;
}
interface LogSource {
  id: string;
  version: string;
  captured_at: string;
  truncated: boolean;
}
interface LogResult {
  text: string;
  truncated: boolean;
  sources: LogSource[];
  error: string;
}
const errors: Record<string, string> = {
  log_unavailable: "此日志尚未生成或已超过保留期限。旧任务不会补生成过程日志。",
  container_unavailable: "未找到运行容器，请查看部署日志或历史归档。",
  log_read_failed: "节点无法读取日志，请检查 Docker 与节点日志目录。",
};

export function LogViewer({
  project,
  target,
  Dialog,
  close,
}: {
  project: string;
  target: LogTarget;
  Dialog: ComponentType<{
    title: string;
    close: () => void;
    children: ReactNode;
  }>;
  close: () => void;
}) {
  const [source, setSource] = useState(target.task ? "deployment" : "runtime");
  const [archive, setArchive] = useState("");
  const [sources, setSources] = useState<LogSource[]>([]);
  const [draft, setDraft] = useState({
    tail: "200",
    search: "",
    since: "",
    until: "",
  });
  const [filters, setFilters] = useState(draft);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<LogResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loadedAt, setLoadedAt] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setResult(null);
    setLoadedAt("");
    if (source === "archives" && !archive) setSources([]);
    const body = {
      source: source === "archives" && archive ? "archive" : source,
      tail: Number(filters.tail),
      search: filters.search,
      ...(source === "deployment" ? { task_id: target.task } : {}),
      ...(source === "archives" && archive ? { archive_id: archive } : {}),
      ...(filters.since
        ? { since: new Date(filters.since).toISOString() }
        : {}),
      ...(filters.until
        ? { until: new Date(filters.until).toISOString() }
        : {}),
    };
    api<LogResult>(`/projects/${project}/instances/${target.instance}/logs`, {
      ...send("POST", body),
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setResult(value);
          setLoadedAt(new Date().toISOString());
          if (source === "archives" && !archive) setSources(value.sources);
          if (value.error)
            setError(errors[value.error] || "日志读取失败，请重试。");
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(cause instanceof Error ? cause.message : "日志查询失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [
    project,
    target.instance,
    target.task,
    source,
    archive,
    filters,
    revision,
  ]);
  const download = () => {
    if (!result) return;
    const url = URL.createObjectURL(
      new Blob([result.text], { type: "text/plain;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${project}-${target.environment}-${source}-${target.task || target.instance}.log`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const listing = source === "archives" && !archive;
  return (
    <Dialog title="查看日志" close={close}>
      <div className="log-viewer">
        <p className="muted">
          {project} / {target.environment} · {target.node}
          {target.task && (
            <>
              {" "}
              · 任务 <code>{target.task}</code>
            </>
          )}
        </p>
        <p className="muted small">
          从节点按需读取。历史归档保存容器替换前仍可读取的输出，默认保留 7 天。
        </p>
        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault();
            if (
              draft.since &&
              draft.until &&
              new Date(draft.since) > new Date(draft.until)
            ) {
              setError("开始时间不能晚于结束时间。");
              return;
            }
            setFilters({ ...draft });
            setRevision((v) => v + 1);
          }}
        >
          <div className="log-filters">
            <label>
              日志来源
              <select
                value={source}
                onChange={(event) => {
                  setSource(event.target.value);
                  setArchive("");
                }}
              >
                <option value="runtime">当前运行日志</option>
                {target.task && (
                  <option value="deployment">部署过程日志</option>
                )}
                <option value="archives">历史运行归档</option>
                <option value="failure">最近失败快照</option>
              </select>
            </label>
            <label>
              最多行数
              <select
                value={draft.tail}
                onChange={(event) =>
                  setDraft({ ...draft, tail: event.target.value })
                }
              >
                {[200, 500, 1000, 2000].map((n) => (
                  <option key={n} value={n}>
                    {n} 行
                  </option>
                ))}
              </select>
            </label>
            <label>
              关键词
              <input
                value={draft.search}
                maxLength={80}
                onChange={(event) =>
                  setDraft({ ...draft, search: event.target.value })
                }
                placeholder="在读取范围内匹配文本"
              />
            </label>
            <label>
              开始时间
              <input
                type="datetime-local"
                value={draft.since}
                onChange={(event) =>
                  setDraft({ ...draft, since: event.target.value })
                }
              />
            </label>
            <label>
              结束时间
              <input
                type="datetime-local"
                value={draft.until}
                onChange={(event) =>
                  setDraft({ ...draft, until: event.target.value })
                }
              />
            </label>
          </div>
          {source === "archives" && (
            <label>
              历史归档
              <select
                value={archive}
                onChange={(event) => setArchive(event.target.value)}
              >
                <option value="">选择节点上的归档</option>
                {sources.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.version || "未知版本"} ·{" "}
                    {remoteDate(item.captured_at)}
                    {item.truncated ? " · 归档已截断" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="log-actions">
            <button className="primary" type="submit" disabled={busy}>
              查询
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setRevision((v) => v + 1)}
            >
              刷新
            </button>
            <button
              type="button"
              disabled={busy || !!error || !result?.text || listing}
              onClick={download}
            >
              下载当前结果
            </button>
            {loadedAt && (
              <span className="muted small">读取于 {remoteDate(loadedAt)}</span>
            )}
          </div>
        </form>
        {busy && <p role="status">正在从节点读取日志…</p>}
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        {result?.truncated && (
          <p className="notice">
            结果已截断，仅显示读取范围内最近的日志。可缩小时间范围再次查询。
          </p>
        )}
        {source === "failure" && (
          <p className="muted small">
            这是该实例最近一次失败快照，不一定属于所选任务。旧格式可能没有时间戳，设置时间范围会排除这些行。
          </p>
        )}
        {!busy && !error && listing && (
          <p className="empty">
            {sources.length
              ? "请选择一份历史归档查看。"
              : "暂无历史归档。新版启用后，在 ctl 替换容器前开始归档。"}
          </p>
        )}
        {!listing && (
          <pre className="log-content" aria-label="日志内容" tabIndex={0}>
            {busy ? "" : error ? "" : result?.text || "当前范围没有日志。"}
          </pre>
        )}
      </div>
    </Dialog>
  );
}
