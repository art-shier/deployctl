import {
  useEffect,
  useState,
  type FormEvent,
  type ComponentType,
  type ReactNode,
} from "react";
import { api, send, APIError } from "./api";
import type { SystemRelease } from "./systemUpdates";
type DialogType = ComponentType<{
  title: string;
  children: ReactNode;
  close: () => void;
}>;
const message = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";
const nowLocal = () => {
  const date = new Date();
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
};
export function RegisterSystemRelease({
  Dialog,
  close,
  saved,
}: {
  Dialog: DialogType;
  close: () => void;
  saved: () => void;
}) {
  const [version, setVersion] = useState("");
  const [published, setPublished] = useState(nowLocal);
  const [cli, setCLI] = useState("");
  const [server, setServer] = useState("");
  const [cliSHA, setCLISHA] = useState("");
  const [serverSHA, setServerSHA] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api(
        "/system-updates/releases",
        send("POST", {
          version: version.trim(),
          published_at: new Date(published).toISOString(),
          cli_url: cli.trim(),
          server_url: server.trim(),
          cli_sha256: cliSHA.trim().toLowerCase(),
          server_sha256: serverSHA.trim().toLowerCase(),
        }),
      );
      saved();
    } catch (error) {
      setError(
        error instanceof APIError && error.status === 409
          ? "该版本已登记，但内容不同。请核对现有记录，不能覆盖已有版本。"
          : message(error),
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      title="登记版本"
      close={() => {
        if (!busy) close();
      }}
    >
      <form className="form-stack" onSubmit={submit}>
        <p className="muted">
          登记会保存版本信息，不下载文件，也不会执行更新。下载地址和校验值登记后不可覆盖。
        </p>
        <fieldset disabled={busy} className="remote-fields">
          <div className="form-two">
            <label>
              版本号
              <input
                required
                pattern="v[0-9]+\.[0-9]+\.[0-9]+"
                placeholder="v1.17.0"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
              />
            </label>
            <label>
              发布时间
              <input
                required
                type="datetime-local"
                value={published}
                onChange={(e) => setPublished(e.target.value)}
              />
            </label>
          </div>
          <button
            type="button"
            disabled={!/^v\d+\.\d+\.\d+$/.test(version.trim())}
            onClick={() => {
              const base = `https://github.com/art-shier/deployctl/releases/download/${version.trim()}/`;
              setCLI(base + "deployctl.pyz");
              setServer(base + `ctl-platform-${version.trim()}.tar.gz`);
            }}
          >
            填入 GitHub 下载地址
          </button>
          <label>
            CLI 更新包下载地址
            <input
              required
              type="url"
              pattern="https://.*"
              value={cli}
              onChange={(e) => setCLI(e.target.value)}
            />
          </label>
          <label>
            CLI SHA256
            <input
              required
              pattern="[a-fA-F0-9]{64}"
              maxLength={64}
              value={cliSHA}
              onChange={(e) => setCLISHA(e.target.value)}
            />
          </label>
          <label>
            中心安装包下载地址
            <input
              required
              type="url"
              pattern="https://.*"
              value={server}
              onChange={(e) => setServer(e.target.value)}
            />
          </label>
          <label>
            中心安装包 SHA256
            <input
              required
              pattern="[a-fA-F0-9]{64}"
              maxLength={64}
              value={serverSHA}
              onChange={(e) => setServerSHA(e.target.value)}
            />
          </label>
        </fieldset>
        <p className="small muted">
          请使用安装包对应 .sha256 文件中的摘要。中心包不包含 Docker
          镜像，更新时还需要访问包内指定的镜像仓库。
        </p>
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        <button className="primary" disabled={busy}>
          {busy ? "正在登记…" : "保存版本"}
        </button>
      </form>
    </Dialog>
  );
}
export function VersionRegistrationAccess({
  Dialog,
  close,
}: {
  Dialog: DialogType;
  close: () => void;
}) {
  const [status, setStatus] = useState<{
    configured: boolean;
    created_at: string | null;
  } | null>(null);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    api<{ configured: boolean; created_at: string | null }>(
      "/system-updates/registration-token",
      { signal: abort.signal },
    )
      .then(setStatus)
      .catch((e) => {
        if (!abort.signal.aborted) setError(message(e));
      });
    return () => abort.abort();
  }, []);
  const change = async (revoke: boolean) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setToken("");
    setNotice("");
    try {
      if (revoke) {
        await api("/system-updates/registration-token", { method: "DELETE" });
        setStatus({ configured: false, created_at: null });
        setNotice("登记凭据已撤销。");
      } else {
        const result = await api<{ token: string }>(
          "/system-updates/registration-token",
          send("POST", {}),
        );
        setToken(result.token);
        setStatus({ configured: true, created_at: new Date().toISOString() });
      }
      setConfirmed(false);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      title="流水线登记接入"
      close={() => {
        if (!busy) close();
      }}
    >
      <div className="form-stack">
        <p>
          在 GitHub Actions Variables 中配置 <code>CTL_CENTER_URL</code>{" "}
          为中心的 HTTPS 地址，并将下方专用凭据保存为 Secret{" "}
          <code>CTL_VERSION_REGISTRATION_TOKEN</code>。
        </p>
        <p className="muted">
          该凭据只能登记版本，不能读取配置、部署项目或操作节点。发布成功后流水线会自动登记；失败可单独重跑
          Register released version。
        </p>
        {!status && !error && <p role="status">正在读取凭据状态…</p>}
        {status && (
          <p>{status.configured ? "已配置登记凭据" : "尚未配置登记凭据"}</p>
        )}
        {status?.configured && !token && (
          <label className="scope-option">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            我已了解更换或撤销后，旧凭据立即失效
          </label>
        )}
        {!token && (
          <div className="actions">
            <button
              disabled={busy || !status || (status.configured && !confirmed)}
              onClick={() => void change(false)}
            >
              {status?.configured ? "更换登记凭据" : "生成登记凭据"}
            </button>
            {status?.configured && (
              <button
                className="danger"
                disabled={busy || !confirmed}
                onClick={() => void change(true)}
              >
                撤销登记凭据
              </button>
            )}
          </div>
        )}
        {token && (
          <>
            <p>凭据只显示这一次，请保存到 GitHub Secret 后关闭。</p>
            <label>
              版本登记凭据
              <input readOnly type="password" value={token} />
            </label>
            <button
              onClick={() =>
                void navigator.clipboard
                  .writeText(token)
                  .then(() => setNotice("凭据已复制"))
                  .catch(() => setError("复制失败，请从凭据输入框选择并复制。"))
              }
            >
              复制登记凭据
            </button>
          </>
        )}
        {error && (
          <p role="alert" className="notice error">
            {error}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
      </div>
    </Dialog>
  );
}
export function SystemReleaseDetails({
  release,
  Dialog,
  close,
}: {
  release: SystemRelease;
  Dialog: DialogType;
  close: () => void;
}) {
  return (
    <Dialog title={`版本 ${release.version}`} close={close}>
      <div className="form-stack" style={{ overflowWrap: "anywhere" }}>
        <p>
          发布时间：{new Date(release.published_at).toLocaleString("zh-CN")}
        </p>
        <p>CLI 下载地址：{release.cli_url || "官方固定下载地址"}</p>
        <p>
          CLI SHA256：<code>{release.cli_sha256}</code>
        </p>
        <p>中心包下载地址：{release.server_url || "官方固定下载地址"}</p>
        <p>
          中心包 SHA256：<code>{release.server_sha256}</code>
        </p>
        <p className="muted">
          登记未验证地址可达性。执行更新时才下载并校验文件；失败会在停止服务前结束。
        </p>
      </div>
    </Dialog>
  );
}
