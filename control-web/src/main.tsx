import React, { useEffect, useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowLeft,
  ArrowUpRight,
  Box,
  Check,
  ChevronRight,
  Clock,
  FolderGit2,
  KeyRound,
  LogOut,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Terminal,
  Trash2,
  X,
} from "lucide-react";
import {
  api,
  send,
  APIError,
  type Project,
  type Release,
  type Environment,
  type Token,
  type Receipt,
  type Audit,
  type Defaults,
  type Group,
  type MaskedVariable,
} from "./api";
import {
  draftRows,
  changes,
  editRow,
  toggleRemoval,
  inheritedRows,
  overrideVariable,
  type DraftRow,
} from "./environmentForm";
import { CredentialForm } from "./CredentialForm";
import { RemoveResource } from "./RemoveResource";
import { ImagesView } from "./ImagesView";
import { GroupsView } from "./ProjectGroups";
import { AgentAccess } from "./AgentAccessPage";
import { tokenScopeLabel } from "./tokenScope";
import { projectFormPayload } from "./projectForm";
import "./styles.css";
import "./workspace.css";

const date = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
const message = (err: unknown) =>
  err instanceof Error ? err.message : "操作失败，请重试。";
function ErrorMessage({ text }: { text: string }) {
  return text ? (
    <div className="notice error" role="alert">
      {text}
    </div>
  ) : null;
}
function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty">
      <Box size={30} />
      <h3>{title}</h3>
      <p>{detail}</p>
    </div>
  );
}
function useData<T>(path: string, initial: T, enabled = true) {
  const [data, setData] = useState<T>(initial),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [count, reload] = useState(0);
  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<T>(path, { signal: controller.signal })
      .then(setData)
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, count, enabled]);
  return { data, loading, error, reload: () => reload((v) => v + 1) };
}
function Loading() {
  return (
    <p className="loading" role="status">
      正在加载…
    </p>
  );
}
function canLeavePage() {
  return window.dispatchEvent(
    new Event("ctl-before-navigate", { cancelable: true }),
  );
}
function useDirtyGuard(dirty: boolean) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    const beforeNavigate = (event: Event) => {
      if (dirtyRef.current && !confirm("离开将丢弃未保存的修改，继续？"))
        event.preventDefault();
    };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const click = (event: MouseEvent) => {
      const link = (event.target as Element).closest<HTMLAnchorElement>(
        'a[href^="#"]',
      );
      if (link && link.hash !== location.hash && !canLeavePage()) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("ctl-before-navigate", beforeNavigate);
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("ctl-before-navigate", beforeNavigate);
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, []);
}
function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: React.ReactNode;
  close: () => void;
}) {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const original = document.activeElement as HTMLElement | null;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab") {
        const nodes = [
          ...document.querySelectorAll<HTMLElement>(
            ".modal button,.modal input,.modal select,.modal textarea",
          ),
        ].filter((n) => !(n as HTMLInputElement).disabled);
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    document.querySelector<HTMLElement>(".modal input,.modal button")?.focus();
    return () => {
      document.removeEventListener("keydown", handler);
      original?.focus();
    };
  }, []);
  return (
    <div
      className="overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="section-heading">
          <h2>{title}</h2>
          <button className="icon-button" onClick={close} aria-label="关闭">
            <X size={20} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}
function App() {
  const [session, setSession] = useState<"loading" | "in" | "out">("loading"),
    [sessionError, setSessionError] = useState(""),
    [token, setToken] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [page, setPage] = useState(location.hash.slice(1) || "groups");
  const check = () => {
    setSession("loading");
    setSessionError("");
    api<{ role: string }>("/me")
      .then((v) => {
        if (v.role !== "owner") throw new Error("请使用管理员凭据登录。");
        setSession("in");
      })
      .catch((e) => {
        if (e instanceof APIError && e.status === 401) setSession("out");
        else {
          setSessionError(message(e));
          setSession("out");
        }
      });
  };
  useEffect(() => {
    check();
    const expired = () => setSession("out");
    window.addEventListener("ctl-session-expired", expired);
    const hash = () => setPage(location.hash.slice(1) || "groups");
    window.addEventListener("hashchange", hash);
    return () => {
      window.removeEventListener("ctl-session-expired", expired);
      window.removeEventListener("hashchange", hash);
    };
  }, []);
  const navigate = (next: string, afterRemoval = false) => {
    if (!afterRemoval && next !== location.hash.slice(1) && !canLeavePage())
      return;
    location.hash = next;
    setPage(next);
  };
  const login = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/session", send("POST", { token }));
      setToken("");
      setSession("in");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const logout = async () => {
    if (!canLeavePage()) return;
    setError("");
    try {
      await api("/session", { method: "DELETE" });
      setSession("out");
      setToken("");
      navigate("groups");
    } catch (e) {
      setError(message(e));
    }
  };
  if (session === "loading")
    return (
      <div className="login-layout">
        <Loading />
      </div>
    );
  if (session === "out")
    return (
      <div className="login-layout">
        <div className="login-card">
          <div className="brand">
            <Terminal size={25} />
            <span>
              ctl<span className="brand-dot">.</span>
            </span>
          </div>
          <p className="eyebrow">DEPLOYMENT CONTROL</p>
          <h1>让部署回归简单。</h1>
          <p className="muted">在一个地方管理项目、发布版本与环境配置。</p>
          <form onSubmit={login}>
            <label>
              管理员凭据
              <input
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                required
                placeholder="输入 owner token"
              />
            </label>
            <ErrorMessage text={error || sessionError} />
            <button className="primary wide" disabled={busy}>
              {busy ? "正在登录…" : "进入管理台"}
              <ChevronRight size={18} />
            </button>
          </form>
          <p className="small muted">
            <ShieldCheck size={14} />
            凭据仅用于建立会话，不保存在浏览器。
          </p>
        </div>
      </div>
    );
  const selected = page.startsWith("project/") ? page.split("/")[1] : null;
  const groupSlug = page.startsWith("group/") ? page.split("/")[1] : undefined;
  const inGroups = page === "groups" || !!groupSlug || !!selected;
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <a className="brand" href="#groups">
          <Terminal size={22} />
          <span>
            ctl<span className="brand-dot">.</span>
          </span>
        </a>
        <p className="sidebar-label">部署工作台</p>
        <nav aria-label="主导航">
          <a href="#groups" className={inGroups ? "active" : ""}>
            <FolderGit2 size={18} />
            项目组
          </a>
          <a href="#projects" className={page === "projects" ? "active" : ""}>
            <Box size={18} />
            全部项目
          </a>
          <a href="#tokens" className={page === "tokens" ? "active" : ""}>
            <KeyRound size={18} />
            组凭据
          </a>
          <a href="#audit" className={page === "audit" ? "active" : ""}>
            <Clock size={18} />
            审计
          </a>
          <a href="#agent" className={page === "agent" ? "active" : ""}>
            <Terminal size={18} />
            Agent 接入
          </a>
        </nav>
        <div className="sidebar-account">
          <ShieldCheck size={17} />
          <div>
            <strong>超级管理员</strong>
            <span>管理所有项目组</span>
          </div>
        </div>
      </aside>
      <div className="app-workspace">
        <header className="workspace-topbar">
          <span>部署管理台</span>
          <button className="text-button" aria-label="退出" onClick={logout}>
            <LogOut size={16} />
            退出
          </button>
        </header>
        <main className="main">
          <ErrorMessage text={error} />
          {page === "groups" || groupSlug ? (
            <GroupsView
              key={groupSlug || "groups"}
              slug={groupSlug}
              tab={page.split("/")[2]}
              navigate={navigate}
              Dialog={Modal}
              renderProjects={(g, changed) => (
                <Projects group={g} navigate={navigate} changed={changed} />
              )}
              renderTokens={(g) => <TokensView group={g} />}
              renderEnvironments={(g) => <EnvironmentsView group={g} />}
            />
          ) : page === "tokens" ? (
            <TokensView />
          ) : page === "audit" ? (
            <AuditView />
          ) : page === "agent" ? (
            <AgentAccess />
          ) : selected ? (
            <ProjectView slug={selected} navigate={navigate} />
          ) : (
            <Projects navigate={navigate} />
          )}
        </main>
        <footer className="footer">
          ctl control plane<span>项目、配置与发布集中管理</span>
        </footer>
      </div>
    </div>
  );
}
function ProjectForm({
  group,
  project,
  done,
  close,
}: {
  group?: Group;
  project?: Project;
  done: () => void;
  close: () => void;
}) {
  const groups = useData<Group[]>("/groups", []);
  const [value, setValue] = useState({
      slug: project?.slug ?? "",
      name: project?.name ?? "",
      description: project?.description ?? "",
      repository: project?.repository ?? "",
      image_repository: project?.image_repository ?? "",
      default_environment: project?.default_environment ?? "prod",
      group: project?.group ?? group?.slug ?? "default",
    }),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(
        project ? `/projects/${project.slug}` : "/projects",
        send(
          project ? "PATCH" : "POST",
          projectFormPayload(
            value,
            project ? project.group || "default" : undefined,
          ),
        ),
      );
      done();
      close();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={save} className="form-stack">
      <div className="form-two">
        <label>
          项目标识
          <input
            required
            pattern="[a-z][a-z0-9]*(-[a-z0-9]+)*"
            maxLength={48}
            disabled={!!project}
            value={value.slug}
            onChange={(e) => setValue({ ...value, slug: e.target.value })}
            placeholder="notes"
          />
        </label>
        <label>
          项目名称
          <input
            required
            maxLength={128}
            value={value.name}
            onChange={(e) => setValue({ ...value, name: e.target.value })}
            placeholder="Notes"
          />
        </label>
      </div>
      <label>
        项目说明
        <textarea
          rows={2}
          maxLength={2048}
          value={value.description}
          onChange={(e) => setValue({ ...value, description: e.target.value })}
        />
      </label>
      <label>
        代码仓库
        <input
          value={value.repository}
          onChange={(e) => setValue({ ...value, repository: e.target.value })}
          placeholder="https://github.com/art-shier/notes"
        />
      </label>
      <label>
        镜像仓库
        <input
          value={value.image_repository}
          onChange={(e) =>
            setValue({ ...value, image_repository: e.target.value })
          }
          placeholder="留空使用平台 Registry / 项目标识"
        />
      </label>
      <label>
        所属项目组
        <select
          value={value.group}
          disabled={!!group || groups.loading || !!groups.error}
          onChange={(e) => setValue({ ...value, group: e.target.value })}
        >
          {groups.data.map((g) => (
            <option value={g.slug} key={g.slug}>
              {g.name} ({g.slug})
            </option>
          ))}
        </select>
      </label>
      <ErrorMessage text={groups.error} />
      {groups.error && (
        <button type="button" onClick={groups.reload}>
          重新加载项目组
        </button>
      )}
      <label>
        默认环境
        <input
          required
          maxLength={32}
          pattern="[a-z][a-z0-9]*(-[a-z0-9]+)*"
          value={value.default_environment}
          onChange={(e) =>
            setValue({ ...value, default_environment: e.target.value })
          }
        />
      </label>
      <ErrorMessage text={error} />
      <div className="actions">
        <button type="button" onClick={close}>
          取消
        </button>
        <button
          className="primary"
          disabled={busy || groups.loading || !!groups.error}
        >
          {busy ? "正在保存…" : project ? "保存项目" : "注册项目"}
        </button>
      </div>
    </form>
  );
}
function Projects({
  navigate,
  group,
  changed,
}: {
  navigate: (page: string) => void;
  group?: Group;
  changed?: () => void;
}) {
  const { data, loading, error, reload } = useData<Project[]>("/projects", []);
  const groups = useData<Group[]>("/groups", []);
  const [create, setCreate] = useState(false),
    [filterGroup, setFilterGroup] = useState(""),
    [search, setSearch] = useState("");
  const [joining, setJoining] = useState(false),
    [selected, setSelected] = useState<string[]>([]),
    [busy, setBusy] = useState(false),
    [actionError, setActionError] = useState("");
  const members = data.filter(
    (p) => !group || (p.group || "default") === group.slug,
  );
  const items = members.filter(
    (p) =>
      (!filterGroup || (p.group || "default") === filterGroup) &&
      `${p.name} ${p.slug}`.toLowerCase().includes(search.toLowerCase()),
  );
  const candidates = data.filter((p) => (p.group || "default") !== group?.slug);
  const move = (p: Project, target: string) =>
    api(
      `/projects/${p.slug}/group`,
      send("PATCH", { expected_group: p.group || "default", group: target }),
    );
  const add = async () => {
    setBusy(true);
    setActionError("");
    const completed: string[] = [];
    try {
      for (const slug of selected) {
        const p = data.find((p) => p.slug === slug);
        if (!p || !group) throw new Error("项目已变化，请刷新后重试。");
        await move(p, group.slug);
        completed.push(slug);
      }
      setJoining(false);
      setSelected([]);
      reload();
      changed?.();
    } catch (e) {
      setSelected(selected.filter((slug) => !completed.includes(slug)));
      reload();
      setActionError(
        `${completed.length ? `已加入 ${completed.length} 个项目，剩余未完成。` : ""}${message(e)}`,
      );
    } finally {
      setBusy(false);
    }
  };
  const remove = async (p: Project) => {
    if (
      !confirm(
        `将「${p.name}」移到 default？当前组凭据将失去该项目的访问权限，default 组凭据将获得权限。`,
      )
    )
      return;
    setBusy(true);
    setActionError("");
    try {
      await move(p, "default");
      reload();
      changed?.();
    } catch (e) {
      setActionError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {!group && (
        <div className="page-heading">
          <div>
            <h1>全部项目</h1>
            <p className="muted">查看所有项目，也可以从项目组进入管理。</p>
          </div>
        </div>
      )}
      <div className="list-toolbar">
        <div className="list-filters">
          <input
            aria-label="搜索项目"
            placeholder="搜索项目名称或标识"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {!group && (
            <select
              aria-label="按项目组筛选"
              value={filterGroup}
              onChange={(e) => setFilterGroup(e.target.value)}
            >
              <option value="">全部项目组</option>
              {groups.data.map((g) => (
                <option key={g.slug} value={g.slug}>
                  {g.name}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="toolbar-actions">
          {group && (
            <button
              disabled={loading || busy || !!error}
              onClick={() => {
                setActionError("");
                setSelected([]);
                setJoining(true);
              }}
            >
              加入已有项目
            </button>
          )}
          <button className="primary" onClick={() => setCreate(true)}>
            <Plus size={16} />
            注册项目
          </button>
        </div>
      </div>
      <ErrorMessage text={error || actionError} />
      {error && <button onClick={reload}>重新加载项目</button>}
      {loading ? (
        <Loading />
      ) : !members.length ? (
        <Empty
          title={group ? "组内还没有项目" : "还没有项目"}
          detail={
            group
              ? "在当前组注册新项目，或将已有项目加入此组。"
              : "先选择项目组，再注册需要部署的服务。"
          }
        />
      ) : (
        <section className="data-panel">
          <div className="data-panel-heading">
            <span>
              {group ? "组内项目" : "项目列表"}
              <span className="count">{items.length}</span>
            </span>
            <button
              className="icon-button"
              aria-label="刷新项目"
              onClick={reload}
            >
              <RefreshCw size={16} />
            </button>
          </div>
          <div className="table-wrap">
            <table className="member-table">
              <thead>
                <tr>
                  <th>项目</th>
                  <th>默认环境</th>
                  <th>镜像仓库</th>
                  {!group && <th>项目组</th>}
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.slug}>
                    <td>
                      <a
                        className="project-name-link"
                        href={`#project/${p.slug}`}
                        aria-label={`打开项目 ${p.name}`}
                      >
                        <span className="project-icon">
                          <FolderGit2 size={18} />
                        </span>
                        <span>
                          <strong>{p.name}</strong>
                          <code className="block muted">{p.slug}</code>
                        </span>
                      </a>
                      {p.description && (
                        <p className="project-row-description muted">
                          {p.description}
                        </p>
                      )}
                    </td>
                    <td data-label="环境">
                      <span className="badge">{p.default_environment}</span>
                    </td>
                    <td data-label="镜像仓库">
                      <code
                        className="repository-cell"
                        title={p.image_repository}
                      >
                        {p.image_repository}
                      </code>
                    </td>
                    {!group && (
                      <td>
                        <a
                          className="inline-link"
                          href={`#group/${p.group || "default"}`}
                        >
                          {p.group || "default"}
                        </a>
                      </td>
                    )}
                    <td className="row-actions">
                      <button
                        className="text-button"
                        onClick={() => navigate(`project/${p.slug}`)}
                      >
                        管理项目
                        <ChevronRight size={14} />
                      </button>
                      {group && group.slug !== "default" && (
                        <button
                          className="text-button danger"
                          disabled={busy}
                          aria-label={`移出项目 ${p.slug}`}
                          onClick={() => void remove(p)}
                        >
                          移出组
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!items.length && <p className="list-empty">没有匹配的项目</p>}
          </div>
        </section>
      )}
      {create && (
        <Modal title="注册项目" close={() => setCreate(false)}>
          <ProjectForm
            group={group}
            done={() => {
              reload();
              changed?.();
            }}
            close={() => setCreate(false)}
          />
        </Modal>
      )}
      {joining && (
        <Modal
          title="加入已有项目"
          close={() => {
            if (!busy) {
              setJoining(false);
              changed?.();
            }
          }}
        >
          <p className="muted">
            每个项目只属于一个组。加入后，原组凭据失去访问权，当前组凭据获得访问权。
          </p>
          <fieldset className="scope-options">
            <legend>选择要加入的项目</legend>
            {candidates.map((p) => (
              <label className="scope-option" key={p.slug}>
                <input
                  type="checkbox"
                  disabled={busy}
                  aria-label={`加入项目 ${p.slug}`}
                  checked={selected.includes(p.slug)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, p.slug]
                        : selected.filter((v) => v !== p.slug),
                    )
                  }
                />
                <span>
                  <strong>{p.name}</strong>
                  <small className="block muted">
                    {p.slug} · 当前组 {p.group || "default"}
                  </small>
                </span>
              </label>
            ))}
            {!candidates.length && (
              <p className="muted">
                没有可加入的项目，可以在当前组注册新项目。
              </p>
            )}
          </fieldset>
          <ErrorMessage text={actionError} />
          <div className="actions">
            <button disabled={busy} onClick={() => setJoining(false)}>
              取消
            </button>
            <button
              className="primary"
              disabled={busy || !selected.length}
              onClick={() => void add()}
            >
              {busy ? "正在加入…" : "确认加入"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
const tabs = [
  ["releases", "版本"],
  ["images", "镜像管理"],
  ["environments", "环境配置"],
  ["receipts", "安装记录"],
] as const;
function ProjectView({
  slug,
  navigate,
}: {
  slug: string;
  navigate: (page: string, afterRemoval?: boolean) => void;
}) {
  const {
      data: project,
      loading,
      error,
      reload,
    } = useData<Project | null>(`/projects/${slug}`, null),
    [tab, setTab] = useState("releases"),
    [edit, setEdit] = useState(false),
    [removing, setRemoving] = useState(false),
    [removeBusy, setRemoveBusy] = useState(false);
  useEffect(() => setTab("releases"), [slug]);
  if (loading) return <Loading />;
  if (error || !project)
    return (
      <>
        <button className="text-button" onClick={() => navigate("projects")}>
          <ArrowLeft size={16} />
          全部项目
        </button>
        <ErrorMessage text={error} />
        <button onClick={reload}>重新加载</button>
      </>
    );
  return (
    <>
      <button
        className="back text-button"
        aria-label="返回所属项目组"
        onClick={() => navigate("group/" + (project.group || "default"))}
      >
        <ArrowLeft size={16} />
        {project.group || "default"} / 组内项目
      </button>
      <div className="page-heading project-heading">
        <div>
          <div className="title-with-icon">
            <div className="project-icon">
              <FolderGit2 size={23} />
            </div>
            <h1>{project.name}</h1>
            <span className="badge">{project.slug}</span>
          </div>
          <p className="muted">
            {project.description || project.image_repository}
          </p>
        </div>
        <button onClick={() => setEdit(true)}>
          <Settings2 size={16} />
          项目设置
        </button>
      </div>
      <div className="tabs" role="tablist" aria-label="项目内容">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => {
              if (id !== tab && canLeavePage()) setTab(id);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div key={slug + tab} role="tabpanel">
        {tab === "releases" ? (
          <ReleasesView project={project} />
        ) : tab === "images" ? (
          <ImagesView project={project} Dialog={Modal} />
        ) : tab === "environments" ? (
          <EnvironmentsView project={project} />
        ) : (
          <ReceiptsView slug={slug} />
        )}
      </div>
      {edit && (
        <Modal title="项目设置" close={() => setEdit(false)}>
          <ProjectForm
            project={project}
            done={reload}
            close={() => setEdit(false)}
          />
          <section className="resource-danger-section">
            <h3>移除项目</h3>
            <p className="muted small">
              从管理台移除后停止后续发布与部署，保留已有运行容器、镜像文件和审计，标识不能复用。
            </p>
            <button
              className="danger"
              onClick={() => {
                setEdit(false);
                setRemoving(true);
              }}
            >
              移除项目
            </button>
          </section>
        </Modal>
      )}
      {removing && (
        <Modal
          title={`移除项目 · ${project.slug}`}
          close={() => {
            if (!removeBusy) setRemoving(false);
          }}
        >
          <RemoveResource
            kind="项目"
            slug={project.slug}
            name={project.name}
            busyChanged={setRemoveBusy}
            cancel={() => setRemoving(false)}
            done={() => {
              setRemoving(false);
              navigate(`group/${project.group || "default"}`, true);
            }}
          />
        </Modal>
      )}
    </>
  );
}
function ReleasesView({ project }: { project: Project }) {
  const { data, loading, error, reload } = useData<Release[]>(
      `/projects/${project.slug}/releases`,
      [],
    ),
    [selected, setSelected] = useState<Release | null>(null),
    [upload, setUpload] = useState(false),
    [actionError, setActionError] = useState(""),
    [busy, setBusy] = useState(false);
  const retire = async (r: Release) => {
    if (!confirm(`停止 ${r.version} 的新安装？历史记录与回滚所需制品会保留。`))
      return;
    setBusy(true);
    try {
      await api(`/projects/${project.slug}/releases/${r.version}/retire`, {
        method: "POST",
      });
      reload();
      setSelected(null);
    } catch (e) {
      setActionError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>发布版本</h2>
          <p className="muted small">安装使用已验证的发布包和固定镜像。</p>
        </div>
        <button onClick={() => setUpload(true)}>
          <Plus size={16} />
          登记版本
        </button>
      </div>
      <ErrorMessage text={error || actionError} />
      {loading ? (
        <Loading />
      ) : data.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>版本</th>
                <th>镜像</th>
                <th>发布时间</th>
                <th>状态</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id}>
                  <td>
                    <button
                      className="link-button mono"
                      onClick={() => setSelected(r)}
                    >
                      {r.version}
                    </button>
                  </td>
                  <td>
                    <code className="truncate" title={r.image}>
                      {r.image}
                    </code>
                  </td>
                  <td className="muted">{date(r.created_at)}</td>
                  <td>
                    <span
                      className={
                        "badge " + (r.status === "published" ? "success" : "")
                      }
                    >
                      {r.status === "published" ? "可安装" : "已停用"}
                    </span>
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      onClick={() => setSelected(r)}
                      aria-label={"查看 " + r.version}
                    >
                      <ChevronRight size={18} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title="还没有发布版本"
          detail="让项目流水线推送镜像并执行 ctl publish，或在此登记标准发布包。"
        />
      )}
      {selected && (
        <Modal title={selected.version} close={() => setSelected(null)}>
          <div className="detail-stack">
            <label>
              镜像<code className="code-block">{selected.image}</code>
            </label>
            <label>
              发布包 SHA256<code className="code-block">{selected.sha256}</code>
            </label>
            <label>
              提交<code>{selected.commit || "未记录"}</code>
            </label>
            <label>
              安装命令
              <code className="code-block">
                ctl install {project.slug} --env {project.default_environment}{" "}
                --version {selected.version}
              </code>
            </label>
            {selected.status === "published" && (
              <button
                className="danger"
                disabled={busy}
                onClick={() => retire(selected)}
              >
                停止新安装
              </button>
            )}
          </div>
        </Modal>
      )}
      {upload && (
        <Modal title="登记发布版本" close={() => setUpload(false)}>
          <UploadRelease
            slug={project.slug}
            done={() => {
              setUpload(false);
              reload();
            }}
          />
        </Modal>
      )}
    </>
  );
}
function UploadRelease({ slug, done }: { slug: string; done: () => void }) {
  const [version, setVersion] = useState(""),
    [file, setFile] = useState<File | null>(null),
    [stable, setStable] = useState(true),
    [proof, setProof] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const raw = await file.arrayBuffer();
      if (raw.byteLength > 10 * 1024 * 1024)
        throw new Error("发布包不能超过 10 MiB。");
      const sum = [
        ...new Uint8Array(await crypto.subtle.digest("SHA-256", raw)),
      ]
        .map((n) => n.toString(16).padStart(2, "0"))
        .join("");
      const form = new FormData();
      form.set("package", file);
      form.set("version", version);
      form.set("sha256", sum);
      if (stable) form.set("channel", "stable");
      await api(`/projects/${slug}/releases`, {
        method: "POST",
        body: form,
        headers: proof ? { "X-Registry-Verification-Token": proof } : undefined,
      });
      done();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
      setProof("");
    }
  };
  return (
    <form onSubmit={submit} className="form-stack">
      <p className="muted">
        镜像需要先推送到项目仓库。上传由 ctl package 生成的标准包。
      </p>
      <label>
        版本号
        <input
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          placeholder="v1.0.0"
          required
        />
      </label>
      <label>
        标准发布包
        <input
          type="file"
          accept=".tar.gz,.gz"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          required
        />
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={stable}
          onChange={(e) => setStable(e.target.checked)}
        />
        发布成功后设为 stable
      </label>
      <details className="registry-proof">
        <summary>外部私有仓库验证（可选）</summary>
        <label>
          短期 pull Token
          <input
            type="password"
            autoComplete="off"
            maxLength={12288}
            value={proof}
            onChange={(e) => setProof(e.target.value)}
          />
        </label>
        <p className="muted small">
          只验证本次镜像，不保存或转交安装主机。托管 Registry 无需填写。
        </p>
      </details>
      <ErrorMessage text={error} />
      <button className="primary" disabled={busy}>
        {busy ? "正在验证并发布…" : "登记版本"}
      </button>
    </form>
  );
}
function EnvironmentsView({
  project,
  group,
}: {
  project?: Project;
  group?: Group;
}) {
  const slug = group?.slug || project!.slug;
  const base = `/${group ? "groups" : "projects"}/${slug}/environments`;
  const { data: envs, loading, error, reload } = useData<string[]>(base, []),
    [env, setEnv] = useState(project?.default_environment || ""),
    [newEnv, setNewEnv] = useState(""),
    [creating, setCreating] = useState(false),
    [creatingBusy, setCreatingBusy] = useState(false),
    [err, setErr] = useState("");
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreatingBusy(true);
    setErr("");
    try {
      await api(
        `${base}/${newEnv}`,
        send("PUT", {
          expected_revision: 0,
          runtime_env: [],
          install_params: [],
        }),
      );
      setEnv(newEnv);
      setNewEnv("");
      setCreating(false);
      reload();
    } catch (e) {
      setErr(message(e));
    } finally {
      setCreatingBusy(false);
    }
  };
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>环境配置</h2>
          <p className="muted small">
            {group
              ? "组内项目继承本环境的业务变量和安装参数；项目同名配置优先。保存后，下次安装或升级生效。"
              : "项目同名配置覆盖组配置；删除项目覆盖后恢复继承。保存后，下次安装或升级生效。"}
          </p>
        </div>
        <button onClick={() => setCreating(true)}>
          <Plus size={16} />
          新增环境
        </button>
      </div>
      <ErrorMessage text={error} />
      {loading ? (
        <Loading />
      ) : error ? (
        <button onClick={reload}>重新加载环境</button>
      ) : (
        <>
          <div className="env-switch" role="tablist" aria-label="选择环境">
            {envs.map((name) => (
              <button
                key={name}
                role="tab"
                aria-selected={(envs.includes(env) ? env : envs[0]) === name}
                onClick={() => {
                  if (
                    name !== (envs.includes(env) ? env : envs[0]) &&
                    canLeavePage()
                  )
                    setEnv(name);
                }}
              >
                {name}
              </button>
            ))}
          </div>
          {envs.length ? (
            <EnvironmentEditor
              key={slug + (envs.includes(env) ? env : envs[0])}
              slug={slug}
              env={envs.includes(env) ? env : envs[0]}
              group={!!group}
            />
          ) : (
            <Empty
              title="尚未配置环境"
              detail="新增环境，为组内项目提供共享变量和安装参数。"
            />
          )}
        </>
      )}
      {creating && (
        <Modal
          title="新增环境"
          close={() => {
            if (!creatingBusy) setCreating(false);
          }}
        >
          <form className="form-stack" onSubmit={create}>
            <label>
              环境名称
              <input
                required
                pattern="[a-z][a-z0-9]*(-[a-z0-9]+)*"
                maxLength={32}
                value={newEnv}
                onChange={(e) => setNewEnv(e.target.value)}
                placeholder="test"
              />
            </label>
            <ErrorMessage text={err} />
            <button className="primary" disabled={creatingBusy}>
              {creatingBusy ? "正在创建…" : "创建环境"}
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
function VariableEditor({
  title,
  detail,
  rows,
  setRows,
  inherited = [],
  source,
}: {
  title: string;
  detail: string;
  rows: DraftRow[];
  setRows: (rows: DraftRow[]) => void;
  inherited?: MaskedVariable[];
  source?: string;
}) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h3>{title}</h3>
          <p className="muted small">{detail}</p>
        </div>
        <button
          className="text-button"
          onClick={() =>
            setRows([
              ...rows,
              { key: "", value: "", secret: false, operation: "set" },
            ])
          }
        >
          <Plus size={16} />
          添加变量
        </button>
      </div>
      {rows.length ? (
        <div className="variable-list">
          {rows.map((row, index) => (
            <div
              key={index}
              className={
                "variable-row " + (row.operation === "remove" ? "removed" : "")
              }
            >
              <label>
                {source ? "项目自有 · 变量名" : "变量名"}
                <input
                  aria-label={title + "变量名 " + (index + 1)}
                  className="mono"
                  value={row.key}
                  disabled={!!row.original || row.operation === "remove"}
                  placeholder="KEY"
                  onChange={(e) =>
                    setRows(editRow(rows, index, { key: e.target.value }))
                  }
                />
              </label>
              <label>
                值
                <input
                  aria-label={title + "值 " + (index + 1)}
                  type={row.secret ? "password" : "text"}
                  value={row.value}
                  disabled={
                    row.operation === "remove" ||
                    (row.secret && row.operation === "keep")
                  }
                  placeholder={
                    row.secret && row.operation === "keep"
                      ? "已配置 · 保持原值"
                      : "允许空值"
                  }
                  autoComplete="off"
                  onChange={(e) =>
                    setRows(
                      editRow(rows, index, {
                        value: e.target.value,
                        operation: "set",
                      }),
                    )
                  }
                />
              </label>
              <div className="variable-options">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={row.secret}
                    disabled={row.operation === "remove"}
                    onChange={(e) =>
                      setRows(
                        editRow(rows, index, { secret: e.target.checked }),
                      )
                    }
                  />
                  秘密
                </label>
                {row.secret && row.original && row.operation !== "remove" && (
                  <button
                    className="link-button"
                    onClick={() =>
                      setRows(
                        editRow(rows, index, {
                          operation: row.operation === "keep" ? "set" : "keep",
                          value: "",
                        }),
                      )
                    }
                  >
                    {row.operation === "keep" ? "替换值" : "保持原值"}
                  </button>
                )}
              </div>
              <button
                className="icon-button"
                aria-label={
                  (row.operation === "remove" ? "恢复 " : "删除 ") +
                  (row.key || "变量")
                }
                onClick={() => setRows(toggleRemoval(rows, index))}
              >
                {row.operation === "remove" ? (
                  <RefreshCw size={16} />
                ) : (
                  <Trash2 size={16} />
                )}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted small panel-empty">
          {source && inheritedRows(rows, inherited).length
            ? "尚无项目自有变量，当前使用下方的组配置。"
            : "尚未配置变量。添加后，下次部署会自动获取。"}
        </p>
      )}
      {inheritedRows(rows, inherited).map((value) => (
        <div className="inherited-variable" key={value.key}>
          <div>
            <code>{value.key}</code>
            <small className="block muted">继承自项目组 {source}</small>
          </div>
          <span className="mono">
            {value.secret
              ? "已配置 · 秘密值"
              : value.value === ""
                ? "（空值）"
                : value.value}
          </span>
          <button
            className="text-button"
            onClick={() => setRows(overrideVariable(rows, value))}
            aria-label={`覆盖 ${value.key}`}
          >
            项目覆盖
          </button>
        </div>
      ))}
    </section>
  );
}
function EnvironmentEditor({
  slug,
  env,
  group = false,
}: {
  slug: string;
  env: string;
  group?: boolean;
}) {
  const path = `/${group ? "groups" : "projects"}/${slug}/environments/${env}`;
  const { data, loading, error, reload } = useData<Environment | null>(
      path,
      null,
    ),
    releases = useData<Release[]>(`/projects/${slug}/releases`, [], !group);
  const [runtime, setRuntime] = useState<DraftRow[]>([]),
    [params, setParams] = useState<DraftRow[]>([]),
    [defaults, setDefaults] = useState<Defaults>({}),
    [target, setTarget] = useState("stable"),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [err, setErr] = useState(""),
    [conflict, setConflict] = useState<Environment | null>(null),
    [preview, setPreview] = useState(false),
    [baseline, setBaseline] = useState("");
  const snapshot = (
    runtime: DraftRow[],
    params: DraftRow[],
    defaults: Defaults,
    target: string,
  ) => JSON.stringify({ runtime, params, defaults, target });
  const dirty =
    !!baseline && snapshot(runtime, params, defaults, target) !== baseline;
  useDirtyGuard(dirty);
  useEffect(() => {
    if (data) {
      setRuntime(draftRows(data.runtime_env));
      setParams(draftRows(data.install_params));
      setDefaults(data.deployment_defaults || {});
      setTarget(data.target_version || "stable");
      setBaseline(
        snapshot(
          draftRows(data.runtime_env),
          draftRows(data.install_params),
          data.deployment_defaults || {},
          data.target_version || "stable",
        ),
      );
    }
  }, [data]);
  const save = async () => {
    if (!data) return;
    setBusy(true);
    setErr("");
    try {
      const result = await api<Environment>(
        path,
        send("PUT", {
          expected_revision: data.revision,
          runtime_env: changes(runtime),
          install_params: changes(params),
          ...(!group
            ? { deployment_defaults: defaults, target_version: target }
            : {}),
        }),
      );
      setPreview(false);
      setNotice(`已保存修订 ${result.revision}。下次安装或升级时生效。`);
      setConflict(null);
      setBaseline(snapshot(runtime, params, defaults, target));
      reload();
    } catch (e) {
      setErr(message(e));
      if (e instanceof APIError && e.status === 409) {
        setPreview(false);
        try {
          setConflict(await api<Environment>(path));
        } catch {}
      }
    } finally {
      setBusy(false);
    }
  };
  const requestSave = () => {
    setNotice("");
    try {
      changes(runtime);
      changes(params);
      setErr("");
      setPreview(true);
    } catch (e) {
      setErr(message(e));
    }
  };
  if (loading) return <Loading />;
  if (error || !data)
    return (
      <>
        <ErrorMessage text={error} />
        <button onClick={reload}>重新加载</button>
      </>
    );
  const keyNames = [...runtime, ...params]
    .filter((r) => r.operation !== "keep")
    .map((r) => r.key + (r.operation === "remove" ? "（删除）" : "（设置）"));
  return (
    <>
      <div className="revision-line">
        <span className="badge">
          <Clock size={13} />
          修订 {data.revision}
        </span>
        <span className="muted small">
          {data.created_at ? date(data.created_at) : "尚无项目覆盖"}
        </span>
        <span className="source-label">
          {group
            ? "来源：项目组 · 组内项目继承"
            : `项目覆盖优先${data.group_source ? ` · 继承自项目组 ${data.group_source.slug} 修订 ${data.group_source.revision}` : ""}`}
        </span>
      </div>
      {notice && (
        <div className="notice" role="status">
          <Check size={16} />
          {notice}
        </div>
      )}
      <ErrorMessage text={err} />
      {conflict && (
        <section className="notice conflict">
          <div>
            <strong>
              服务器已有修订 {conflict.revision}，当前草稿已保留。
            </strong>
            <p>
              服务器键名：
              {[...conflict.runtime_env, ...conflict.install_params]
                .map((v) => v.key)
                .join("、") || "无"}
              。重新加载后可基于最新配置修改。
            </p>
          </div>
          <button
            onClick={() => {
              if (confirm("重新加载将丢弃当前草稿，确定？")) {
                setConflict(null);
                setErr("");
                setNotice("");
                reload();
              }
            }}
          >
            加载最新配置
          </button>
        </section>
      )}
      {!group && (
        <section className="panel target-panel">
          <div>
            <h3>安装目标</h3>
            <p className="muted small">
              环境名与版本独立。固定版本，或跟随 stable 通道。
            </p>
          </div>
          <label>
            目标版本
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="stable">stable · 跟随已发布版本</option>
              {!releases.data.some((r) => r.version === target) &&
                target !== "stable" && <option value={target}>{target}</option>}
              {releases.data
                .filter((r) => r.status === "published")
                .map((r) => (
                  <option key={r.id} value={r.version}>
                    {r.version}
                  </option>
                ))}
            </select>
          </label>
        </section>
      )}
      <VariableEditor
        title="业务变量"
        detail="注入应用的运行环境。秘密值保存后不再显示。"
        rows={runtime}
        setRows={setRuntime}
        inherited={group ? [] : data.inherited_runtime_env}
        source={group ? undefined : data.group_source?.slug}
      />
      <VariableEditor
        title="安装参数"
        detail="用于项目的安装准备与初始化，例如管理员邮箱。"
        rows={params}
        setRows={setParams}
        inherited={group ? [] : data.inherited_install_params}
        source={group ? undefined : data.group_source?.slug}
      />
      {!group && (
        <section className="panel">
          <h3>部署默认值</h3>
          <p className="muted small">
            首次安装使用这些默认值。已安装主机保留当前端口，命令行可指定新端口。
          </p>
          <div className="defaults-grid">
            {(
              [
                ["host_port", "主机端口", "8080"],
                ["bind_address", "绑定地址", "127.0.0.1"],
                ["memory_limit", "内存上限", "512m"],
                ["cpus", "CPU 上限", "1"],
              ] as const
            ).map(([key, label, placeholder]) => (
              <label key={key}>
                {label}
                <input
                  type={
                    key === "host_port" || key === "cpus" ? "number" : "text"
                  }
                  min={key === "cpus" ? 0.1 : 1}
                  max={
                    key === "host_port"
                      ? 65535
                      : key === "cpus"
                        ? 128
                        : undefined
                  }
                  step={key === "cpus" ? 0.1 : 1}
                  value={defaults[key] ?? ""}
                  placeholder={"例如 " + placeholder}
                  onChange={(e) => {
                    const next = { ...defaults };
                    if (e.target.value === "") delete next[key];
                    else
                      Object.assign(next, {
                        [key]:
                          key === "cpus" || key === "host_port"
                            ? Number(e.target.value)
                            : e.target.value,
                      });
                    setDefaults(next);
                  }}
                />
              </label>
            ))}
          </div>
        </section>
      )}
      <div className="save-bar">
        <p className="small muted">
          {dirty ? "有未保存的修改。" : ""}保存配置不会重启正在运行的服务。
        </p>
        <button
          className="primary"
          disabled={busy || !!conflict}
          onClick={requestSave}
        >
          审阅并保存
        </button>
      </div>
      {preview && (
        <Modal title="确认配置变更" close={() => setPreview(false)}>
          <p>
            将创建 {env} 的修订 {data.revision + 1}。
          </p>
          <p className="muted small">
            变量变化：
            {keyNames.join("、") ||
              "无值变更（包含秘密标记、目标版本或默认值调整）"}
          </p>
          <p className="muted small">
            {!group && `目标版本：${target}。`}秘密值不会在此显示。
          </p>
          <div className="actions">
            <button onClick={() => setPreview(false)}>返回编辑</button>
            <button className="primary" onClick={save} disabled={busy}>
              {busy ? "正在保存…" : "保存新修订"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
function TokensView({ group }: { group?: Group }) {
  const { data, loading, error, reload } = useData<Token[]>("/tokens", []),
    [create, setCreate] = useState(false),
    [editing, setEditing] = useState<Token | null>(null),
    [reveal, setReveal] = useState<Token | null>(null),
    [formDirty, setFormDirty] = useState(false),
    [saved, setSaved] = useState(""),
    [fresh, setFresh] = useState(""),
    [err, setErr] = useState(""),
    [revoking, setRevoking] = useState("");
  useDirtyGuard(formDirty);
  const revoke = async (t: Token) => {
    if (
      !confirm(
        `撤销「${t.name}」？使用此凭据的流水线和安装操作将无法继续访问。`,
      )
    )
      return;
    setRevoking(t.id);
    setErr("");
    try {
      await api(`/tokens/${t.id}`, { method: "DELETE" });
      reload();
    } catch (e) {
      setErr(message(e));
    } finally {
      setRevoking("");
    }
  };
  const items = data.filter(
    (t) => !t.revoked && (!group || t.groups?.includes(group.slug)),
  );
  const closeForm = () => {
    if (!formDirty || confirm("关闭将丢弃未保存的凭据修改，继续？")) {
      setCreate(false);
      setEditing(null);
      setFormDirty(false);
    }
  };
  useEffect(() => {
    const hide = () => {
      setFresh("");
      setReveal(null);
    };
    window.addEventListener("hashchange", hide);
    return () => window.removeEventListener("hashchange", hide);
  }, []);
  const Title = group ? "h2" : "h1";
  return (
    <>
      <div className={group ? "section-heading" : "page-heading"}>
        <div>
          <Title>{group ? "组授权" : "组凭据"}</Title>
          <p className="muted small">
            为项目组创建发布或部署凭据。组内所有项目共用授权，部署凭据仍受环境限制。
          </p>
        </div>
        <button
          className="primary"
          onClick={() => {
            setFresh("");
            setCreate(true);
          }}
        >
          <Plus size={16} />
          {group ? "创建组凭据" : "创建凭据"}
        </button>
      </div>
      <ErrorMessage text={error || err} />
      {saved && (
        <p className="notice" role="status">
          {saved}
        </p>
      )}
      {fresh && (
        <section className="notice token-result">
          <KeyRound size={20} />
          <div>
            <strong>
              请安全保存凭据。管理员可稍后通过“查看 Token”再次查看。
            </strong>
            <code className="code-block" data-testid="new-token">
              {fresh}
            </code>
            <button className="text-button" onClick={() => setFresh("")}>
              已保存，关闭展示
            </button>
          </div>
        </section>
      )}
      {loading ? (
        <Loading />
      ) : items.length ? (
        <div className="table-wrap">
          <table className="credential-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>权限</th>
                <th>环境</th>
                <th>授权范围</th>
                <th>到期</th>
                <th>状态</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.id}>
                  <td>
                    {t.name}
                    <small className="block muted mono">
                      {t.id.slice(0, 8)}
                    </small>
                  </td>
                  <td data-label="权限">
                    {t.role === "publisher"
                      ? "发布、项目管理与配置读写（授权范围内）"
                      : "部署"}
                  </td>
                  <td data-label="环境">
                    {t.environments.length
                      ? t.environments.join(", ")
                      : t.role === "publisher"
                        ? "全部环境"
                        : "未授权环境"}
                  </td>
                  <td data-label="授权范围">
                    {tokenScopeLabel(t) || "无授权项目"}
                  </td>
                  <td className="muted" data-label="到期">
                    {date(t.expires_at)}
                  </td>
                  <td data-label="状态">
                    <span className="badge">
                      {t.revoked
                        ? "已撤销"
                        : new Date(t.expires_at) < new Date()
                          ? "已到期"
                          : "有效"}
                    </span>
                  </td>
                  <td className="credential-actions">
                    {!t.revoked && (
                      <>
                        <button
                          className="text-button"
                          onClick={() => {
                            setFormDirty(false);
                            setEditing(t);
                          }}
                        >
                          编辑
                        </button>
                        <button
                          className="text-button"
                          onClick={() => setReveal(t)}
                        >
                          查看 Token
                        </button>
                      </>
                    )}
                    {!t.revoked && (
                      <button
                        className="text-button danger"
                        disabled={!!revoking}
                        onClick={() => revoke(t)}
                      >
                        {revoking === t.id ? "正在撤销…" : "撤销"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title="还没有访问凭据"
          detail="分别为流水线和安装服务器创建所需权限的凭据。"
        />
      )}
      {(create || editing) && (
        <Modal
          title={editing ? "编辑访问凭据" : "创建访问凭据"}
          close={closeForm}
        >
          <CredentialForm
            group={group}
            token={editing || undefined}
            dirtyChanged={setFormDirty}
            done={(token) => {
              setFresh(token);
              setSaved(editing ? "凭据已保存，授权立即生效。" : "");
              setCreate(false);
              setEditing(null);
              setFormDirty(false);
              reload();
            }}
          />
        </Modal>
      )}
      {reveal && (
        <TokenReveal
          token={reveal}
          close={() => setReveal(null)}
          changed={reload}
        />
      )}
    </>
  );
}
function TokenReveal({
  token,
  close,
  changed,
}: {
  token: Token;
  close: () => void;
  changed: () => void;
}) {
  const rotation = useRef<AbortController | null>(null);
  const [value, setValue] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(true),
    [legacy, setLegacy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api<{ token: string }>(`/tokens/${token.id}/secret`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) setValue(result.token);
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(message(e));
          setLegacy(e instanceof APIError && e.status === 409);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => {
      controller.abort();
      rotation.current?.abort();
    };
  }, [token.id]);
  const rotate = async () => {
    if (
      !confirm(
        `重新生成「${token.name}」的 Token？旧 Token 将立即失效，请把新 Token 更新到 CLI、服务器和 CI。授权范围保持不变。`,
      )
    )
      return;
    setBusy(true);
    setError("");
    const controller = new AbortController();
    rotation.current = controller;
    try {
      const result = await api<{ token: string }>(
        `/tokens/${token.id}/rotate`,
        { ...send("POST", {}), signal: controller.signal },
      );
      if (!controller.signal.aborted) {
        setValue(result.token);
        setLegacy(false);
        changed();
      }
    } catch (e) {
      if (!controller.signal.aborted) setError(message(e));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  return (
    <Modal title={`查看 Token · ${token.name}`} close={close}>
      <dl className="token-details">
        <dt>权限</dt>
        <dd>
          {token.role === "publisher"
            ? "发布、项目管理与配置读写（授权范围内）"
            : "部署"}
        </dd>
        <dt>授权范围</dt>
        <dd>{tokenScopeLabel(token) || "无"}</dd>
        <dt>环境</dt>
        <dd>
          {token.environments.length
            ? token.environments.join("、")
            : token.role === "publisher"
              ? "全部环境（配置读写）"
              : "未授权环境"}
        </dd>
        <dt>到期</dt>
        <dd>{date(token.expires_at)}</dd>
      </dl>
      <p className="muted small">
        Token 加密保存，仅管理员可查看。关闭此窗口或离开当前页面后将隐藏展示。
      </p>
      {busy && <Loading />}
      <ErrorMessage text={error} />
      {value && (
        <code className="code-block" data-testid="revealed-token">
          {value}
        </code>
      )}
      {legacy && (
        <button
          className="danger"
          disabled={busy}
          onClick={() => void rotate()}
        >
          重新生成 Token
        </button>
      )}
    </Modal>
  );
}
function ReceiptsView({ slug }: { slug: string }) {
  const { data, loading, error, reload } = useData<Receipt[]>(
      `/projects/${slug}/receipts`,
      [],
    ),
    releases = useData<Release[]>(`/projects/${slug}/releases`, []);
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>安装记录</h2>
          <p className="muted small">
            由客户端上报的历史结果，当前主机状态请使用 ctl status 查看。
          </p>
        </div>
        <button aria-label="刷新安装记录" onClick={reload}>
          <RefreshCw size={16} />
          刷新
        </button>
      </div>
      <ErrorMessage text={error} />
      {loading ? (
        <Loading />
      ) : data.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>环境 / 主机</th>
                <th>版本</th>
                <th>配置修订 ID</th>
                <th>结果</th>
                <th>上报时间</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id}>
                  <td>
                    {r.environment}
                    <small className="block muted mono">
                      {r.host_id.slice(0, 12)}
                    </small>
                  </td>
                  <td>
                    <code>
                      {releases.data.find((v) => v.id === r.release_id)
                        ?.version || r.release_id.slice(0, 12)}
                    </code>
                  </td>
                  <td>
                    <code>{r.configuration_revision.slice(0, 12)}</code>
                  </td>
                  <td>
                    <span
                      className={"badge " + (r.success ? "success" : "failed")}
                    >
                      {r.success ? "成功" : "失败"}
                    </span>
                  </td>
                  <td className="muted">{date(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title="暂无安装记录"
          detail="使用平台模式安装后，客户端会自动上报安装结果。"
        />
      )}
    </>
  );
}
function AuditView() {
  const { data, loading, error, reload } = useData<Audit[]>("/audit", []);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">CHANGE HISTORY</p>
          <h1>审计记录</h1>
          <p className="muted">项目、配置与权限的每次变更。</p>
        </div>
        <button onClick={reload}>
          <RefreshCw size={16} />
          刷新
        </button>
      </div>
      <ErrorMessage text={error} />
      {loading ? (
        <Loading />
      ) : data.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>操作</th>
                <th>项目 / 环境</th>
                <th>变更键名</th>
                <th>操作者</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id}>
                  <td>
                    <code>{r.action}</code>
                  </td>
                  <td>
                    {r.project}
                    {r.environment && " / " + r.environment}
                  </td>
                  <td className="muted">{r.keys?.join(", ") || "—"}</td>
                  <td>
                    <code>
                      {r.actor === "owner" ? "管理员" : r.actor.slice(0, 8)}
                    </code>
                  </td>
                  <td className="muted">{date(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title="暂无变更记录"
          detail="项目注册和配置变更将在这里记录，不保存秘密值。"
        />
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
