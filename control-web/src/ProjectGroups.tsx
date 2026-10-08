import { useEffect, useState, type FormEvent } from "react";
import { Plus, Settings2 } from "lucide-react";
import { api, send, type Group, type Project } from "./api";

export function GroupsView() {
  const [groups, setGroups] = useState<Group[]>([]),
    [projects, setProjects] = useState<Project[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [editing, setEditing] = useState<Group | null | undefined>(),
    [busy, setBusy] = useState(false),
    [value, setValue] = useState({ slug: "", name: "", description: "" });
  const load = async () => {
    setLoading(true);
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
  }, []);
  const edit = (group: Group | null) => {
    setEditing(group);
    setValue(group || { slug: "", name: "", description: "" });
    setError("");
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(
        editing ? `/groups/${editing.slug}` : "/groups",
        send(editing ? "PATCH" : "POST", value),
      );
      setEditing(undefined);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">WORKSPACE</p>
          <h1>项目组</h1>
          <p className="muted">
            组织项目，为服务器授权整个项目组。已有项目默认属于 default。
          </p>
        </div>
        <button className="primary" onClick={() => edit(null)}>
          <Plus size={18} />
          创建项目组
        </button>
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
          <button onClick={() => void load()}>重新加载</button>
        </div>
      )}
      {editing !== undefined && (
        <section className="panel">
          <h2>{editing ? "编辑项目组" : "新项目组"}</h2>
          <form className="form-stack" onSubmit={save}>
            <label>
              项目组标识
              <input
                required
                disabled={!!editing}
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
              />
            </label>
            <label>
              项目组说明
              <textarea
                rows={2}
                maxLength={2048}
                value={value.description}
                onChange={(e) =>
                  setValue({ ...value, description: e.target.value })
                }
              />
            </label>
            <div className="actions">
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditing(undefined)}
              >
                取消
              </button>
              <button className="primary" disabled={busy}>
                {busy ? "正在保存…" : "保存项目组"}
              </button>
            </div>
          </form>
        </section>
      )}
      {loading ? (
        <p role="status">正在加载…</p>
      ) : (
        <div className="project-grid">
          {groups.map((g) => (
            <section className="panel" key={g.slug}>
              <div className="section-heading">
                <h2>{g.name}</h2>
                <button
                  className="icon-button"
                  aria-label={`编辑项目组 ${g.slug}`}
                  onClick={() => edit(g)}
                >
                  <Settings2 size={18} />
                </button>
              </div>
              <p className="muted">{g.description || "还没有项目组说明"}</p>
              <div className="project-meta">
                <code>{g.slug}</code>
                <span className="badge">
                  {
                    projects.filter((p) => (p.group || "default") === g.slug)
                      .length
                  }{" "}
                  个项目
                </span>
              </div>
            </section>
          ))}
        </div>
      )}
    </>
  );
}
