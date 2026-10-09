import { useState, type FormEvent } from "react";
import { api } from "./api";

export function RemoveResource({
  kind,
  slug,
  name,
  done,
  cancel,
  busyChanged,
}: {
  kind: "项目" | "项目组";
  slug: string;
  name: string;
  done: () => void;
  cancel: () => void;
  busyChanged: (busy: boolean) => void;
}) {
  const [confirmation, setConfirmation] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const remove = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || confirmation !== slug) return;
    setBusy(true);
    busyChanged(true);
    setError("");
    try {
      await api<void>(
        `/${kind === "项目" ? "projects" : "groups"}/${encodeURIComponent(slug)}`,
        { method: "DELETE" },
      );
      done();
    } catch (e) {
      setError(e instanceof Error ? e.message : "移除失败，请重试。");
    } finally {
      setBusy(false);
      busyChanged(false);
    }
  };
  return (
    <form className="form-stack" onSubmit={remove}>
      <p>
        将移除{kind}「{name}」<code className="block mono">{slug}</code>
      </p>
      <div className="notice error removal-effects">
        <p>
          从管理台移除，停止后续发布/部署；已有运行容器、镜像文件和审计保留，标识不能复用。
        </p>
        <p>
          配置、发布版本与历史记录保留，不会删除服务器上的运行服务。
          {kind === "项目组"
            ? "项目组必须为空；已保留的组凭据范围不会自动扩大。"
            : "使用此项目的凭据将失去对该项目的访问权限。"}
        </p>
      </div>
      <label>
        输入{kind}标识确认
        <input
          autoComplete="off"
          spellCheck={false}
          className="mono"
          value={confirmation}
          disabled={busy}
          onChange={(e) => setConfirmation(e.target.value)}
          placeholder={slug}
        />
      </label>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      <div className="actions">
        <button type="button" disabled={busy} onClick={cancel}>
          取消
        </button>
        <button className="danger" disabled={busy || confirmation !== slug}>
          {busy ? "正在移除…" : `确认移除${kind}`}
        </button>
      </div>
    </form>
  );
}
