import {
  useEffect,
  useState,
  type ComponentType,
  type ReactNode,
  type FormEvent,
} from "react";
import { Plus, RefreshCw } from "lucide-react";
import { api, send, type Group, type Project } from "./api";
import { nodeLabel, remoteDate, type DeploymentNode } from "./remote";

type DialogType = ComponentType<{
  title: string;
  children: ReactNode;
  close: () => void;
}>;
interface Enrollment {
  enrollment_token: string;
  expires_at: string;
}
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";
const toggle = (items: string[], value: string, checked: boolean) =>
  checked ? [...items, value] : items.filter((item) => item !== value);

function NodeForm({
  node,
  saved,
  close,
}: {
  node?: DeploymentNode;
  saved: (result?: Enrollment) => void;
  close: () => void;
}) {
  const [name, setName] = useState(node?.name || "");
  const [groups, setGroups] = useState(node?.groups || []);
  const [projects, setProjects] = useState(node?.projects || []);
  const [excluded, setExcluded] = useState(node?.excluded_projects || []);
  const [environments, setEnvironments] = useState(
    node?.environments.join(",") || "prod",
  );
  const [options, setOptions] = useState<{
    groups: Group[];
    projects: Project[];
    environments: string[];
  }>({ groups: [], projects: [], environments: [] });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, retry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const get = <T,>(path: string) =>
      api<T>(path, { signal: controller.signal });
    setLoading(true);
    setLoadError("");
    Promise.all([get<Group[]>("/groups"), get<Project[]>("/projects")])
      .then(async ([groups, projects]) => {
        const envs = await Promise.all([
          ...projects.map((project) =>
            get<string[]>(`/projects/${project.slug}/environments`),
          ),
          ...groups.map((group) =>
            get<string[]>(`/groups/${group.slug}/environments`),
          ),
        ]);
        if (!controller.signal.aborted)
          setOptions({
            groups,
            projects,
            environments: [...new Set(envs.flat())].sort(),
          });
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setLoadError(errorText(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [revision]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setError("");
    const envs = [
      ...new Set(
        environments
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
      ),
    ];
    if (!name.trim()) {
      setError("请填写节点名称。");
      return;
    }
    if (!envs.length) {
      setError("请授权至少一个环境。");
      return;
    }
    if (envs.some((value) => !/^[a-z][a-z0-9]*(-[a-z0-9]+)*$/.test(value))) {
      setError("请填写有效环境名称，多个环境以逗号分隔。");
      return;
    }
    setBusy(true);
    try {
      const body = {
        name: name.trim(),
        groups,
        projects,
        excluded_projects: excluded,
        environments: envs,
      };
      if (node) {
        await api(
          `/nodes/${node.id}`,
          send("PATCH", { ...body, enabled: node.enabled }),
        );
        saved();
      } else saved(await api<Enrollment>("/nodes", send("POST", body)));
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="form-stack" onSubmit={save}>
      <fieldset disabled={busy} className="remote-fields">
        <label>
          节点名称
          <input
            required
            maxLength={128}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        {loading && <p role="status">正在加载授权范围…</p>}
        {loadError && (
          <div className="notice error" role="alert">
            {loadError}
            <button type="button" onClick={() => retry((value) => value + 1)}>
              重试授权范围
            </button>
          </div>
        )}
        <fieldset className="scope-options">
          <legend>授权项目组</legend>
          {[
            ...new Set([
              ...options.groups.map((group) => group.slug),
              ...groups,
            ]),
          ].map((slug) => (
            <label key={slug} className="scope-option">
              <input
                type="checkbox"
                aria-label={`授权项目组 ${slug}`}
                checked={groups.includes(slug)}
                onChange={(event) =>
                  setGroups(toggle(groups, slug, event.target.checked))
                }
              />
              <span>
                {options.groups.find((group) => group.slug === slug)?.name ||
                  slug}
                <code className="block muted">{slug}</code>
              </span>
            </label>
          ))}
        </fieldset>
        <fieldset className="scope-options">
          <legend>单独授权项目</legend>
          {[
            ...new Set([
              ...options.projects.map((project) => project.slug),
              ...projects,
            ]),
          ].map((slug) => (
            <label key={slug} className="scope-option">
              <input
                type="checkbox"
                aria-label={`授权项目 ${slug}`}
                checked={projects.includes(slug)}
                onChange={(event) =>
                  setProjects(toggle(projects, slug, event.target.checked))
                }
              />
              <span>{slug}</span>
            </label>
          ))}
        </fieldset>
        <fieldset className="scope-options">
          <legend>排除项目</legend>
          {[
            ...new Set([
              ...options.projects.map((project) => project.slug),
              ...excluded,
            ]),
          ].map((slug) => (
            <label key={slug} className="scope-option">
              <input
                type="checkbox"
                aria-label={`排除项目 ${slug}`}
                checked={excluded.includes(slug)}
                onChange={(event) =>
                  setExcluded(toggle(excluded, slug, event.target.checked))
                }
              />
              <span>{slug}</span>
            </label>
          ))}
        </fieldset>
        <label>
          允许环境
          <input
            value={environments}
            list="node-environment-options"
            onChange={(event) => setEnvironments(event.target.value)}
            placeholder="prod,staging"
          />
        </label>
        <datalist id="node-environment-options">
          {options.environments.map((environment) => (
            <option key={environment} value={environment} />
          ))}
        </datalist>
        <p className="muted small">
          至少授权一个环境，多个环境用逗号分隔。项目授权取组与单独项目的并集，再应用排除项。
        </p>
      </fieldset>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="button" disabled={busy} onClick={close}>
          取消
        </button>
        <button className="primary" disabled={busy || loading || !!loadError}>
          {busy ? "正在保存…" : "保存节点"}
        </button>
      </div>
    </form>
  );
}

export function NodesPage({ Dialog }: { Dialog: DialogType }) {
  const [nodes, setNodes] = useState<DeploymentNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<DeploymentNode | "new" | null>(null);
  const [fresh, setFresh] = useState<Enrollment | null>(null);
  const [revision, reload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const nodes = await api<DeploymentNode[]>("/nodes", {
          signal: controller.signal,
        });
        if (!controller.signal.aborted) {
          setNodes(nodes);
          setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorText(cause));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(refresh, 10000);
        }
      }
    };
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [revision]);
  const change = async (node: DeploymentNode, reenroll: boolean) => {
    if (busy) return;
    if (
      reenroll &&
      !confirm(
        `重新登记「${node.name}」将使旧节点身份立即失效。有活动任务时无法重新登记。继续？`,
      )
    )
      return;
    if (
      !reenroll &&
      node.enabled &&
      !confirm(
        `禁用「${node.name}」后将拒绝新任务和物料访问，已有业务容器继续运行。继续？`,
      )
    )
      return;
    setBusy(node.id);
    setError("");
    try {
      if (reenroll)
        setFresh(
          await api<Enrollment>(
            `/nodes/${node.id}/enrollment`,
            send("POST", {}),
          ),
        );
      else
        await api(
          `/nodes/${node.id}`,
          send("PATCH", {
            name: node.name,
            groups: node.groups,
            projects: node.projects,
            excluded_projects: node.excluded_projects,
            environments: node.environments,
            enabled: !node.enabled,
          }),
        );
      reload((value) => value + 1);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="remote-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DEPLOYMENT NODES</p>
          <h1>部署节点</h1>
          <p className="muted">管理远程 Linux Docker 主机及部署授权。</p>
        </div>
        <div className="actions">
          <button onClick={() => reload((value) => value + 1)}>
            <RefreshCw size={16} />
            刷新
          </button>
          <button className="primary" onClick={() => setEditing("new")}>
            <Plus size={16} />
            创建节点
          </button>
        </div>
      </div>
      <p className="notice">
        在线表示最近 90
        秒收到已登记节点的心跳。失联与业务健康分别记录。禁用后停止接收新任务，业务容器继续运行。
      </p>
      {error && (
        <div className="notice error" role="alert">
          {error}
          <button onClick={() => reload((value) => value + 1)}>重试</button>
        </div>
      )}
      {loading ? (
        <p className="loading" role="status">
          正在加载节点…
        </p>
      ) : nodes.length ? (
        <div className="table-wrap">
          <table aria-label="部署节点">
            <thead>
              <tr>
                <th>节点</th>
                <th>连接状态</th>
                <th>授权范围</th>
                <th>Agent / Docker</th>
                <th>最后心跳</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node) => (
                <tr key={node.id}>
                  <td>
                    <strong>{node.name}</strong>
                    <code className="block muted">{node.id}</code>
                  </td>
                  <td>
                    <span className="badge">{nodeLabel(node)}</span>
                  </td>
                  <td>
                    <span>组 {node.groups.join("、") || "无"}</span>
                    <small className="block">
                      项目 {node.projects.join("、") || "无"}
                    </small>
                    <small className="block">
                      排除 {node.excluded_projects.join("、") || "无"}
                    </small>
                    <small className="block">
                      环境 {node.environments.join("、") || "未授权"}
                    </small>
                  </td>
                  <td>
                    {node.agent_version || "未登记"}
                    <small className="block muted">
                      {node.platform || "—"} · Docker{" "}
                      {node.last_seen
                        ? node.docker_available
                          ? "可用"
                          : "不可用"
                        : "未知"}
                    </small>
                  </td>
                  <td>{remoteDate(node.last_seen)}</td>
                  <td>
                    <div className="actions">
                      <button
                        disabled={!!busy}
                        onClick={() => setEditing(node)}
                        aria-label={`编辑节点 ${node.name}`}
                      >
                        编辑授权
                      </button>
                      <button
                        disabled={!!busy}
                        className={node.enabled ? "danger" : ""}
                        onClick={() => void change(node, false)}
                        aria-label={`${node.enabled ? "禁用" : "启用"}节点 ${node.name}`}
                      >
                        {node.enabled ? "禁用" : "启用"}
                      </button>
                      <button
                        disabled={!!busy}
                        onClick={() => void change(node, true)}
                        aria-label={`重新登记节点 ${node.name}`}
                      >
                        重新登记
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">
          <h3>暂无部署节点</h3>
          <p>创建节点后，在 Linux 主机上使用一次性凭据登记 Agent。</p>
        </div>
      )}
      {editing && (
        <Dialog
          title={editing === "new" ? "创建节点" : "编辑节点授权"}
          close={() => setEditing(null)}
        >
          <NodeForm
            node={editing === "new" ? undefined : editing}
            close={() => setEditing(null)}
            saved={(result) => {
              setEditing(null);
              setFresh(result || null);
              reload((value) => value + 1);
            }}
          />
        </Dialog>
      )}
      {fresh && (
        <Dialog title="一次性节点注册" close={() => setFresh(null)}>
          <div className="form-stack">
            <p>
              请立即保存凭据。有效期 15
              分钟，只展示本次。关闭后需重新登记生成新凭据。
            </p>
            <label>
              一次性注册凭据
              <textarea
                readOnly
                spellCheck={false}
                autoComplete="off"
                value={fresh.enrollment_token}
                onFocus={(event) => event.target.select()}
              />
            </label>
            <p className="muted small">
              到期时间 {remoteDate(fresh.expires_at)}
            </p>
            <p>
              在节点执行{" "}
              <code>ctl agent enroll --server {location.origin}</code>
              ，按隐藏输入提示粘贴凭据。
            </p>
            <button className="primary" onClick={() => setFresh(null)}>
              已保存，关闭凭据
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
