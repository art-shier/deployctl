import { useEffect, useState, type FormEvent } from "react";
import { api, send, type Group, type Project, type Token } from "./api";
import { tokenCoversProject } from "./tokenScope";

const toggle = (items: string[], value: string, checked: boolean) =>
  checked
    ? [...new Set([...items, value])]
    : items.filter((item) => item !== value);
const localDate = (value: string) => {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};

export function CredentialForm({
  group,
  token,
  done,
  dirtyChanged,
}: {
  group?: Group;
  token?: Token;
  done: (token: string) => void;
  dirtyChanged: (dirty: boolean) => void;
}) {
  const [options, setOptions] = useState<{
    groups: Group[];
    projects: Project[];
  }>({ groups: [], projects: [] });
  const [loading, setLoading] = useState(true),
    [loadError, setLoadError] = useState("");
  const [name, setName] = useState(token?.name || ""),
    [role, setRole] = useState(token?.role || "deployer");
  const [groups, setGroups] = useState(
    token?.groups || (group ? [group.slug] : []),
  );
  const [projects, setProjects] = useState(
    token?.projects || (token?.project ? [token.project] : []),
  );
  const [excluded, setExcluded] = useState(token?.excluded_projects || []);
  const [environments, setEnvironments] = useState(
    token ? token.environments.join(",") : "prod",
  );
  const [days, setDays] = useState(90),
    [expiry, setExpiry] = useState(token ? localDate(token.expires_at) : "");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [groups, projects] = await Promise.all([
        api<Group[]>("/groups"),
        api<Project[]>("/projects"),
      ]);
      setOptions({ groups, projects });
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "读取授权范围失败");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const scope = { groups, projects, excluded_projects: excluded };
  const covered = options.projects.filter((project) =>
    tokenCoversProject(scope, project),
  );
  const mark = () => dirtyChanged(true);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (!groups.length && !projects.length)
        throw new Error("至少授权一个项目组或项目。");
      const parsedEnvironments = [
        ...new Set(
          environments
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
        ),
      ];
      if (
        (role === "deployer" && !parsedEnvironments.length) ||
        parsedEnvironments.some(
          (value) => !/^[a-z][a-z0-9]*(-[a-z0-9]+)*$/.test(value),
        )
      )
        throw new Error("请填写有效环境名称，多个环境以逗号分隔。");
      const expiresAt = token
        ? expiry === localDate(token.expires_at)
          ? token.expires_at
          : new Date(expiry).toISOString()
        : new Date(Date.now() + days * 86400000).toISOString();
      const value = await api<{ token?: string }>(
        token ? `/tokens/${token.id}` : "/tokens",
        send(token ? "PATCH" : "POST", {
          name,
          role,
          groups,
          projects,
          excluded_projects: excluded,
          environments: parsedEnvironments,
          expires_at: expiresAt,
        }),
      );
      dirtyChanged(false);
      done(value.token || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="form-stack" onSubmit={save} onChange={mark}>
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
        <select
          value={role}
          onChange={(e) => {
            setRole(e.target.value);
            if (e.target.value === "deployer" && !environments.trim())
              setEnvironments("prod");
          }}
        >
          <option value="deployer">部署 · 拉取镜像与指定环境配置</option>
          <option value="publisher">
            发布、项目管理与配置读写（授权范围内）
          </option>
        </select>
      </label>
      {group && !token && (
        <div className="scope-summary">
          <strong>{`授权项目组：${group.slug}`}</strong>
          <span className="muted">{group.name}</span>
        </div>
      )}
      {loading ? (
        <p role="status">正在加载授权范围…</p>
      ) : (
        <>
          <fieldset className="scope-options">
            <legend>授权项目组</legend>
            {options.groups.map((g) => (
              <label className="scope-option" key={g.slug}>
                <input
                  type="checkbox"
                  aria-label={`授权项目组 ${g.slug}`}
                  checked={groups.includes(g.slug)}
                  onChange={(e) =>
                    setGroups(toggle(groups, g.slug, e.target.checked))
                  }
                />
                <span>
                  {g.name}
                  <code className="block muted">{g.slug}</code>
                </span>
              </label>
            ))}
            {groups
              .filter((slug) => !options.groups.some((g) => g.slug === slug))
              .map((slug) => (
                <label className="scope-option" key={slug}>
                  <input
                    type="checkbox"
                    aria-label={`授权项目组 ${slug}`}
                    checked
                    onChange={() => setGroups(toggle(groups, slug, false))}
                  />
                  <span>
                    <code>{slug}</code>
                    <small className="block muted">
                      已移除 · 取消此范围后可保存
                    </small>
                  </span>
                </label>
              ))}
            {!options.groups.length && (
              <p className="muted small">没有可选项目组。</p>
            )}
          </fieldset>
          <fieldset className="scope-options">
            <legend>项目授权与排除</legend>
            <p className="muted small">
              取消组内项目的勾选会明确排除该项目；单独授权的项目不受组变动影响。
            </p>
            {options.projects.map((project) => {
              const inherited = groups.includes(project.group || "default");
              const explicit = projects.includes(project.slug);
              const denied = excluded.includes(project.slug);
              return (
                <div className="credential-project-option" key={project.slug}>
                  <label className="scope-option">
                    <input
                      type="checkbox"
                      aria-label={`允许项目 ${project.slug}`}
                      checked={tokenCoversProject(scope, project)}
                      onChange={(e) => {
                        setExcluded(
                          toggle(excluded, project.slug, !e.target.checked),
                        );
                        if (!e.target.checked || !inherited)
                          setProjects(
                            toggle(projects, project.slug, e.target.checked),
                          );
                      }}
                    />
                    <span>
                      {project.name}
                      <code className="block muted">{project.slug}</code>
                      <small className="block muted">
                        {denied
                          ? "已排除"
                          : explicit
                            ? "单独授权 · 有效"
                            : inherited
                              ? `来自项目组 ${project.group || "default"} · 有效`
                              : "未授权"}
                      </small>
                    </span>
                  </label>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      aria-label={`单独授权项目 ${project.slug}`}
                      checked={explicit}
                      onChange={(e) => {
                        setProjects(
                          toggle(projects, project.slug, e.target.checked),
                        );
                        if (e.target.checked)
                          setExcluded(toggle(excluded, project.slug, false));
                      }}
                    />
                    单独授权
                  </label>
                </div>
              );
            })}
            {projects
              .filter(
                (slug) =>
                  !options.projects.some((project) => project.slug === slug),
              )
              .map((slug) => (
                <label className="scope-option" key={slug}>
                  <input
                    type="checkbox"
                    aria-label={`单独授权项目 ${slug}`}
                    checked
                    onChange={() => setProjects(toggle(projects, slug, false))}
                  />
                  <span>
                    <code>{slug}</code>
                    <small className="block muted">
                      已移除 · 单独授权，取消此范围后可保存
                    </small>
                  </span>
                </label>
              ))}
            {excluded
              .filter((slug) => !options.projects.some((p) => p.slug === slug))
              .map((slug) => (
                <label className="scope-option" key={slug}>
                  <input
                    type="checkbox"
                    aria-label={`排除项目 ${slug}`}
                    checked
                    onChange={() => setExcluded(toggle(excluded, slug, false))}
                  />
                  <span>{slug} · 已排除 · 已移除</span>
                </label>
              ))}
            {!options.projects.length && (
              <p className="muted small">没有可选项目。</p>
            )}
          </fieldset>
          <section className="scope-preview">
            <strong>当前覆盖 {covered.length} 个项目</strong>
            <div className="scope-projects">
              {covered.map((project) => (
                <span className="badge" key={project.slug}>
                  {project.name}
                </span>
              ))}
            </div>
            <p className="muted small">
              以后加入所选组的项目自动获得授权，排除项优先。
              {role === "deployer"
                ? "只能读取所选环境配置及拉取镜像，不能发布或管理项目。"
                : "可以推送镜像与发布版本，可管理授权项目并读取、修改环境配置（含秘密值）；不能管理项目组、其他范围或访问凭据。"}
            </p>
          </section>
        </>
      )}
      {loadError && (
        <div className="notice error" role="alert">
          {loadError}
          <button type="button" onClick={() => void load()}>
            重新加载授权范围
          </button>
        </div>
      )}
      {
        <label>
          允许的环境
          <input
            required={role === "deployer"}
            value={environments}
            onChange={(e) => setEnvironments(e.target.value)}
            placeholder="prod,test"
          />
          <span className="muted small">
            {role === "publisher"
              ? "可选，留空允许授权项目的全部环境。配置读写、新项目初始环境及修改默认环境均受限制；发布、镜像与其他项目资料不受此环境过滤。"
              : "仅允许这些环境，多个环境以逗号分隔。"}
          </span>
        </label>
      }
      {token ? (
        <label>
          到期时间
          <input
            type="datetime-local"
            required
            value={expiry}
            onChange={(e) => setExpiry(e.target.value)}
          />
          <span className="muted small">
            使用本地时间，保存后权限与到期时间立即生效。
          </span>
        </label>
      ) : (
        <label>
          有效天数
          <input
            type="number"
            min={1}
            max={365}
            required
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </label>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      <button
        className="primary"
        disabled={
          busy || (!groups.length && !projects.length) || loading || !!loadError
        }
      >
        {busy
          ? "正在保存…"
          : token
            ? "保存凭据"
            : group
              ? "创建组凭据"
              : "创建凭据"}
      </button>
    </form>
  );
}
