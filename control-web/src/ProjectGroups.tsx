import {
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
  type ComponentType,
} from "react";
import {
  ArrowLeft,
  ChevronRight,
  FolderGit2,
  Plus,
  RefreshCw,
} from "lucide-react";
import { api, send, type Group, type Project } from "./api";

type DialogProps = { title: string; close: () => void; children: ReactNode };
type Props = {
  slug?: string;
  tab?: string;
  navigate: (page: string) => void;
  Dialog: ComponentType<DialogProps>;
  renderProjects: (group: Group, changed: () => void) => ReactNode;
  renderTokens: (group: Group) => ReactNode;
  renderEnvironments: (group: Group) => ReactNode;
};

export function GroupsView({
  slug,
  tab = "projects",
  navigate,
  Dialog,
  renderProjects,
  renderTokens,
  renderEnvironments,
}: Props) {
  const [groups, setGroups] = useState<Group[]>([]),
    [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [creating, setCreating] = useState(false),
    [search, setSearch] = useState("");
  const load = async () => {
    setError("");
    try {
      const [g, p] = await Promise.all([
        api<Group[]>("/groups"),
        api<Project[]>("/projects"),
      ]);
      setGroups(g);
      setProjects(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, [slug]);
  const selected = groups.find((g) => g.slug === slug);
  const count = (g: Group) =>
    projects.filter((p) => (p.group || "default") === g.slug).length;
  const filtered = groups.filter((g) =>
    `${g.slug} ${g.name} ${g.description}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const tabs = [
    ["projects", `组内项目${selected ? ` (${count(selected)})` : ""}`],
    ["access", "组授权"],
    ["environments", "环境配置"],
    ["settings", "组设置"],
  ];
  return (
    <>
      {slug && (
        <button className="back text-button" onClick={() => navigate("groups")}>
          <ArrowLeft size={16} />
          返回项目组
        </button>
      )}
      <div className="page-heading">
        <div>
          <h1>{slug ? selected?.name || "项目组" : "项目组"}</h1>
          <p className="muted">
            {slug
              ? selected?.description || "管理组内项目、发布与部署授权。"
              : "先选择项目组，再管理项目和授权。"}
          </p>
          {selected && <code className="group-identity">{selected.slug}</code>}
        </div>
        {!slug && (
          <button className="primary" onClick={() => setCreating(true)}>
            <Plus size={16} />
            创建项目组
          </button>
        )}
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
          <button onClick={() => void load()}>重新加载</button>
        </div>
      )}
      {loading ? (
        <p className="loading" role="status">
          正在加载项目组…
        </p>
      ) : error ? null : slug ? (
        selected ? (
          <>
            <div className="tabs" role="tablist" aria-label="项目组内容">
              {tabs.map(([id, label]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={tab === id}
                  onClick={() => navigate(`group/${slug}/${id}`)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div role="tabpanel">
              {tab === "access" ? (
                renderTokens(selected)
              ) : tab === "environments" ? (
                renderEnvironments(selected)
              ) : tab === "settings" ? (
                <section className="panel group-settings">
                  <h2>项目组设置</h2>
                  <p className="muted small">
                    名称和说明可修改，项目组标识保持不变。
                  </p>
                  <GroupForm group={selected} done={() => void load()} />
                </section>
              ) : (
                renderProjects(selected, () => void load())
              )}
            </div>
          </>
        ) : (
          <div className="notice error" role="alert">
            该项目组不存在。请返回项目组列表。
          </div>
        )
      ) : (
        <>
          <div className="list-toolbar">
            <input
              className="group-search"
              aria-label="搜索项目组"
              placeholder="搜索项目组名称或标识"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <span className="muted small">
              {groups.length} 个项目组 · {projects.length} 个项目
            </span>
          </div>
          <section className="data-panel">
            <div className="data-panel-heading">
              <span>项目组列表</span>
              <button
                className="icon-button"
                aria-label="刷新项目组"
                onClick={() => void load()}
              >
                <RefreshCw size={16} />
              </button>
            </div>
            <div className="group-list">
              {filtered.map((g) => (
                <a
                  className="group-row"
                  href={`#group/${g.slug}`}
                  aria-label={`查看项目组 ${g.slug}`}
                  key={g.slug}
                >
                  <span className="group-icon">
                    <FolderGit2 size={22} />
                  </span>
                  <div className="group-row-title">
                    <h2>{g.name}</h2>
                    <code>{g.slug}</code>
                    {g.description && <p className="muted">{g.description}</p>}
                  </div>
                  <span className="group-member-count">
                    <strong>{count(g)}</strong> 个项目
                  </span>
                  <span className="group-row-action">
                    查看项目
                    <ChevronRight size={16} />
                  </span>
                </a>
              ))}
            </div>
            {!filtered.length && <p className="list-empty">没有匹配的项目组</p>}
          </section>
          <p className="page-note">
            为项目组创建一份凭据，即可发布或部署组内的多个服务。未分组的旧项目归入
            default。
          </p>
        </>
      )}
      {creating && (
        <Dialog title="创建项目组" close={() => setCreating(false)}>
          <GroupForm
            done={() => {
              setCreating(false);
              void load();
            }}
          />
        </Dialog>
      )}
    </>
  );
}

function GroupForm({ group, done }: { group?: Group; done: () => void }) {
  const [value, setValue] = useState({
    slug: group?.slug || "",
    name: group?.name || "",
    description: group?.description || "",
  });
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  useEffect(() => {
    setValue({
      slug: group?.slug || "",
      name: group?.name || "",
      description: group?.description || "",
    });
  }, [group]);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await api(
        group ? `/groups/${group.slug}` : "/groups",
        send(group ? "PATCH" : "POST", value),
      );
      setSaved(true);
      done();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="form-stack" onSubmit={save}>
      <label>
        项目组标识
        <input
          required
          disabled={!!group}
          maxLength={48}
          pattern="[a-z][a-z0-9]*(-[a-z0-9]+)*"
          value={value.slug}
          onChange={(e) => setValue({ ...value, slug: e.target.value })}
          placeholder="apps"
        />
      </label>
      <label>
        项目组名称
        <input
          required
          maxLength={128}
          value={value.name}
          onChange={(e) => setValue({ ...value, name: e.target.value })}
          placeholder="业务服务"
        />
      </label>
      <label>
        项目组说明
        <textarea
          rows={3}
          maxLength={2048}
          value={value.description}
          onChange={(e) => setValue({ ...value, description: e.target.value })}
          placeholder="这个组管理哪些服务"
        />
      </label>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {saved && group && <p role="status">已保存项目组设置。</p>}
      <div className="actions">
        <button className="primary" disabled={busy}>
          {busy ? "正在保存…" : "保存项目组"}
        </button>
      </div>
    </form>
  );
}
