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
} from "./api";
import {
  draftRows,
  changes,
  editRow,
  toggleRemoval,
  type DraftRow,
} from "./environmentForm";
import { ImagesView } from "./ImagesView";
import { GroupsView } from "./ProjectGroups";
import { tokenCoversProject, tokenScopeLabel } from "./tokenScope";
import { projectFormPayload } from "./projectForm";
import "./styles.css";

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
function useData<T>(path: string, initial: T) {
  const [data, setData] = useState<T>(initial),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [count, reload] = useState(0);
  useEffect(() => {
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
  }, [path, count]);
  return { data, loading, error, reload: () => reload((v) => v + 1) };
}
function Loading() {
  return (
    <p className="loading" role="status">
      正在加载…
    </p>
  );
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
  const [page, setPage] = useState(location.hash.slice(1) || "projects");
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
    const hash = () => setPage(location.hash.slice(1) || "projects");
    window.addEventListener("hashchange", hash);
    return () => {
      window.removeEventListener("ctl-session-expired", expired);
      window.removeEventListener("hashchange", hash);
    };
  }, []);
  const navigate = (next: string) => {
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
    setError("");
    try {
      await api("/session", { method: "DELETE" });
      setSession("out");
      setToken("");
      navigate("projects");
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
  return (
    <>
      <header className="topbar">
        <a className="brand" href="#projects">
          <Terminal size={22} />
          <span>
            ctl<span className="brand-dot">.</span>
          </span>
        </a>
        <span className="top-divider" />
        <span className="top-title">部署管理台</span>
        <span className="badge owner-badge">超级管理员</span>
        <nav aria-label="主导航">
          <a
            href="#projects"
            className={
              page === "projects" || page.startsWith("project/") ? "active" : ""
            }
          >
            项目
          </a>
          <a href="#groups" className={page === "groups" ? "active" : ""}>
            项目组
          </a>
          <a href="#tokens" className={page === "tokens" ? "active" : ""}>
            访问凭据
          </a>
          <a href="#audit" className={page === "audit" ? "active" : ""}>
            审计
          </a>
        </nav>
        <button
          className="text-button logout"
          aria-label="退出"
          onClick={logout}
        >
          <LogOut size={16} />
          <span>退出</span>
        </button>
      </header>
      <main className="main">
        <ErrorMessage text={error} />
        {page === "groups" ? (
          <GroupsView />
        ) : page === "tokens" ? (
          <TokensView />
        ) : page === "audit" ? (
          <AuditView />
        ) : selected ? (
          <ProjectView slug={selected} navigate={navigate} />
        ) : (
          <Projects navigate={navigate} />
        )}
      </main>
      <footer className="footer">
        ctl control plane<span>配置集中管理 · 部署在你的服务器执行</span>
      </footer>
    </>
  );
}
function ProjectForm({
  project,
  done,
  close,
}: {
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
      group: project?.group ?? "default",
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
          disabled={groups.loading || !!groups.error}
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
function Projects({ navigate }: { navigate: (page: string) => void }) {
  const { data, loading, error, reload } = useData<Project[]>("/projects", []),
    [create, setCreate] = useState(false),
    [group, setGroup] = useState("");
  const groups = useData<Group[]>("/groups", []);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">WORKSPACE</p>
          <h1>项目</h1>
          <p className="muted">从发布到部署，所有项目井然有序。</p>
        </div>
        <button className="primary" onClick={() => setCreate(true)}>
          <Plus size={18} />
          注册项目
        </button>
      </div>
      <ErrorMessage text={error} />
      <label className="group-filter">
        按项目组筛选
        <select
          aria-label="按项目组筛选"
          value={group}
          onChange={(e) => setGroup(e.target.value)}
        >
          <option value="">全部项目组</option>
          {groups.data.map((g) => (
            <option value={g.slug} key={g.slug}>
              {g.name} ({g.slug})
            </option>
          ))}
        </select>
      </label>
      <ErrorMessage text={groups.error} />
      {error && <button onClick={reload}>重新加载</button>}
      {loading ? (
        <Loading />
      ) : data.length ? (
        <>
          <div className="section-heading">
            <span className="small muted">
              全部项目 <span className="count">{data.length}</span>
            </span>
            <button
              className="icon-button"
              aria-label="刷新项目"
              onClick={reload}
            >
              <RefreshCw size={16} />
            </button>
          </div>
          <div className="project-grid">
            {data
              .filter((p) => !group || (p.group || "default") === group)
              .map((p) => (
                <button
                  key={p.slug}
                  className="project-card"
                  onClick={() => navigate("project/" + p.slug)}
                >
                  <div className="project-card-top">
                    <div className="project-icon">
                      <FolderGit2 size={23} />
                    </div>
                    <ArrowUpRight size={18} />
                  </div>
                  <h2>{p.name}</h2>
                  <p className="muted">{p.description || "还没有项目说明"}</p>
                  <div className="project-meta">
                    <code>{p.slug}</code>
                    <span className="badge">{p.default_environment}</span>
                    <span className="badge">{p.group || "default"}</span>
                  </div>
                  <div className="project-source">{p.image_repository}</div>
                </button>
              ))}
          </div>
        </>
      ) : (
        <Empty
          title="注册你的第一个项目"
          detail="注册项目后，即可发布镜像和配置安装环境。"
        />
      )}
      {create && (
        <Modal title="注册项目" close={() => setCreate(false)}>
          <ProjectForm done={reload} close={() => setCreate(false)} />
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
  ["tokens", "访问凭据"],
] as const;
function ProjectView({
  slug,
  navigate,
}: {
  slug: string;
  navigate: (page: string) => void;
}) {
  const {
      data: project,
      loading,
      error,
      reload,
    } = useData<Project | null>(`/projects/${slug}`, null),
    [tab, setTab] = useState("releases"),
    [edit, setEdit] = useState(false);
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
      <button className="back text-button" onClick={() => navigate("projects")}>
        <ArrowLeft size={16} />
        全部项目
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
            onClick={() => setTab(id)}
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
        ) : tab === "receipts" ? (
          <ReceiptsView slug={slug} />
        ) : (
          <TokensView project={project} />
        )}
      </div>
      {edit && (
        <Modal title="项目设置" close={() => setEdit(false)}>
          <ProjectForm
            project={project}
            done={reload}
            close={() => setEdit(false)}
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
function EnvironmentsView({ project }: { project: Project }) {
  const {
      data: envs,
      loading,
      error,
      reload,
    } = useData<string[]>(`/projects/${project.slug}/environments`, []),
    [env, setEnv] = useState(project.default_environment),
    [newEnv, setNewEnv] = useState(""),
    [creating, setCreating] = useState(false),
    [err, setErr] = useState("");
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api(
        `/projects/${project.slug}/environments/${newEnv}`,
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
    }
  };
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>环境配置</h2>
          <p className="muted small">
            管理台配置优先于安装时的同名参数；保存后，下次安装或升级生效。
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
      ) : (
        <>
          <div className="env-switch" role="tablist" aria-label="选择环境">
            {envs.map((name) => (
              <button
                key={name}
                role="tab"
                aria-selected={env === name}
                onClick={() => {
                  if (name !== env && confirm("切换环境会关闭当前草稿，继续？"))
                    setEnv(name);
                }}
              >
                {name}
              </button>
            ))}
          </div>
          <EnvironmentEditor
            key={project.slug + env}
            slug={project.slug}
            env={env}
          />
        </>
      )}
      {creating && (
        <Modal title="新增环境" close={() => setCreating(false)}>
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
            <button className="primary">创建环境</button>
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
}: {
  title: string;
  detail: string;
  rows: DraftRow[];
  setRows: (rows: DraftRow[]) => void;
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
                变量名
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
          尚未配置变量。添加后，下次部署会自动获取。
        </p>
      )}
    </section>
  );
}
function EnvironmentEditor({ slug, env }: { slug: string; env: string }) {
  const { data, loading, error, reload } = useData<Environment | null>(
      `/projects/${slug}/environments/${env}`,
      null,
    ),
    releases = useData<Release[]>(`/projects/${slug}/releases`, []);
  const [runtime, setRuntime] = useState<DraftRow[]>([]),
    [params, setParams] = useState<DraftRow[]>([]),
    [defaults, setDefaults] = useState<Defaults>({}),
    [target, setTarget] = useState("stable"),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [err, setErr] = useState(""),
    [conflict, setConflict] = useState<Environment | null>(null),
    [preview, setPreview] = useState(false);
  useEffect(() => {
    if (data) {
      setRuntime(draftRows(data.runtime_env));
      setParams(draftRows(data.install_params));
      setDefaults(data.deployment_defaults);
      setTarget(data.target_version);
    }
  }, [data]);
  const save = async () => {
    if (!data) return;
    setBusy(true);
    setErr("");
    try {
      const result = await api<Environment>(
        `/projects/${slug}/environments/${env}`,
        send("PUT", {
          expected_revision: data.revision,
          runtime_env: changes(runtime),
          install_params: changes(params),
          deployment_defaults: defaults,
          target_version: target,
        }),
      );
      setPreview(false);
      setNotice(`已保存修订 ${result.revision}。下次安装或升级时生效。`);
      setConflict(null);
      reload();
    } catch (e) {
      setErr(message(e));
      if (e instanceof APIError && e.status === 409) {
        setPreview(false);
        try {
          setConflict(
            await api<Environment>(`/projects/${slug}/environments/${env}`),
          );
        } catch {}
      }
    } finally {
      setBusy(false);
    }
  };
  const requestSave = () => {
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
        <span className="muted small">{date(data.created_at)}</span>
        <span className="source-label">来源：管理台 · 最高优先级</span>
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
                reload();
              }
            }}
          >
            加载最新配置
          </button>
        </section>
      )}
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
      <VariableEditor
        title="业务变量"
        detail="注入应用的运行环境。秘密值保存后不再显示。"
        rows={runtime}
        setRows={setRuntime}
      />
      <VariableEditor
        title="安装参数"
        detail="用于项目的安装准备与初始化，例如管理员邮箱。"
        rows={params}
        setRows={setParams}
      />
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
                type={key === "host_port" || key === "cpus" ? "number" : "text"}
                min={key === "cpus" ? 0.1 : 1}
                max={
                  key === "host_port" ? 65535 : key === "cpus" ? 128 : undefined
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
      <div className="save-bar">
        <p className="small muted">保存配置不会重启正在运行的服务。</p>
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
            目标版本：{target}。秘密值不会在此显示。
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
function TokensView({ project }: { project?: Project }) {
  const { data, loading, error, reload } = useData<Token[]>("/tokens", []),
    [create, setCreate] = useState(false),
    [fresh, setFresh] = useState(""),
    [err, setErr] = useState("");
  const revoke = async (t: Token) => {
    if (
      !confirm(
        `撤销「${t.name}」？使用此凭据的流水线和安装操作将无法继续访问。`,
      )
    )
      return;
    try {
      await api(`/tokens/${t.id}`, { method: "DELETE" });
      reload();
    } catch (e) {
      setErr(message(e));
    }
  };
  const items = project
    ? data.filter((t) => tokenCoversProject(t, project))
    : data;
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>访问凭据</h2>
          <p className="muted small">
            发布凭据用于 CI，部署凭据用于目标服务器。
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
          创建凭据
        </button>
      </div>
      <ErrorMessage text={error || err} />
      {fresh && (
        <section className="notice token-result">
          <KeyRound size={20} />
          <div>
            <strong>凭据只显示这一次，请现在安全保存。</strong>
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
          <table>
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
                  <td>{t.role === "publisher" ? "发布" : "部署"}</td>
                  <td>
                    {t.role === "deployer" ? t.environments.join(", ") : "—"}
                  </td>
                  <td>{tokenScopeLabel(t)}</td>
                  <td className="muted">{date(t.expires_at)}</td>
                  <td>
                    <span className="badge">
                      {t.revoked
                        ? "已撤销"
                        : new Date(t.expires_at) < new Date()
                          ? "已到期"
                          : "有效"}
                    </span>
                  </td>
                  <td>
                    {!t.revoked && (
                      <button
                        className="text-button danger"
                        onClick={() => revoke(t)}
                      >
                        撤销
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
      {create && (
        <Modal title="创建访问凭据" close={() => setCreate(false)}>
          <TokenForm
            project={project}
            done={(token) => {
              setFresh(token);
              setCreate(false);
              reload();
            }}
          />
        </Modal>
      )}
    </>
  );
}
function TokenForm({
  project,
  done,
}: {
  project?: Project;
  done: (token: string) => void;
}) {
  const projectOptions = useData<Project[]>("/projects", []),
    groupOptions = useData<Group[]>("/groups", []);
  const [scope, setScope] = useState<"projects" | "groups">("projects"),
    [selectedProjects, setSelectedProjects] = useState<string[]>(
      project ? [project.slug] : [],
    ),
    [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  const [name, setName] = useState(""),
    [role, setRole] = useState("deployer"),
    [envs, setEnvs] = useState(project?.default_environment || "prod"),
    [days, setDays] = useState(90),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (!(scope === "projects" ? selectedProjects : selectedGroups).length)
        throw new Error("至少选择一个项目或项目组。");
      const value = await api<{ token: string }>(
        "/tokens",
        send("POST", {
          name,
          role,
          projects: scope === "projects" ? selectedProjects : [],
          groups: scope === "groups" ? selectedGroups : [],
          environments:
            role === "deployer"
              ? envs
                  .split(",")
                  .map((v) => v.trim())
                  .filter(Boolean)
              : [],
          expires_at: new Date(Date.now() + days * 86400000).toISOString(),
        }),
      );
      done(value.token);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="form-stack" onSubmit={save}>
      <label>
        名称
        <input
          required
          maxLength={128}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="prod-server / github-actions"
        />
      </label>
      <label>
        权限
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="deployer">部署 · 拉取镜像与指定环境配置</option>
          <option value="publisher">发布 · 推送镜像与发布包</option>
        </select>
      </label>
      <label>
        授权范围
        <select
          value={scope}
          onChange={(e) => setScope(e.target.value as "projects" | "groups")}
        >
          <option value="projects">指定项目 · 固定范围</option>
          <option value="groups">项目组 · 包含以后加入的项目</option>
        </select>
      </label>
      <fieldset className="scope-options">
        <legend>{scope === "projects" ? "选择项目" : "选择项目组"}</legend>
        {scope === "projects"
          ? projectOptions.data.map((p) => (
              <label className="scope-option" key={p.slug}>
                <input
                  type="checkbox"
                  aria-label={`授权项目 ${p.slug}`}
                  checked={selectedProjects.includes(p.slug)}
                  onChange={(e) =>
                    setSelectedProjects(
                      e.target.checked
                        ? [...selectedProjects, p.slug]
                        : selectedProjects.filter((id) => id !== p.slug),
                    )
                  }
                />
                <span>
                  {p.name} <code>{p.slug}</code>
                </span>
              </label>
            ))
          : groupOptions.data.map((g) => (
              <label className="scope-option" key={g.slug}>
                <input
                  type="checkbox"
                  aria-label={`授权项目组 ${g.slug}`}
                  checked={selectedGroups.includes(g.slug)}
                  onChange={(e) =>
                    setSelectedGroups(
                      e.target.checked
                        ? [...selectedGroups, g.slug]
                        : selectedGroups.filter((id) => id !== g.slug),
                    )
                  }
                />
                <span>
                  {g.name} <code>{g.slug}</code>
                </span>
              </label>
            ))}
      </fieldset>
      <p className="muted small">
        项目组凭据包含组内当前和以后加入的项目。项目移出组后，下一次请求重新判断访问范围。
      </p>
      <ErrorMessage text={projectOptions.error || groupOptions.error} />
      {(projectOptions.error || groupOptions.error) && (
        <button
          type="button"
          onClick={() => {
            projectOptions.reload();
            groupOptions.reload();
          }}
        >
          重新加载授权范围
        </button>
      )}
      {role === "deployer" && (
        <label>
          允许的环境
          <input
            required
            value={envs}
            onChange={(e) => setEnvs(e.target.value)}
            placeholder="prod,test"
          />
          <span className="muted small">多个环境以逗号分隔。</span>
        </label>
      )}
      <label>
        有效天数
        <input
          type="number"
          min={1}
          max={365}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        />
      </label>
      <ErrorMessage text={error} />
      <button
        className="primary"
        disabled={
          busy ||
          projectOptions.loading ||
          groupOptions.loading ||
          !!projectOptions.error ||
          !!groupOptions.error
        }
      >
        {busy ? "正在创建…" : "创建凭据"}
      </button>
    </form>
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
