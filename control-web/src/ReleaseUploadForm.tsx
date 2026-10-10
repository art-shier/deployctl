import { useState } from "react";
import type { Project } from "./api";
import { uploadRelease } from "./releaseUpload";

export function ReleaseUpload({
  project,
  done,
}: {
  project: Project;
  done: () => void;
}) {
  const [version, setVersion] = useState(""),
    [file, setFile] = useState<File | null>(null),
    [stable, setStable] = useState(true),
    [proof, setProof] = useState(""),
    [commit, setCommit] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [progress, setProgress] = useState(0);
  const staticProject = project.deployment_type === "static";
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setProgress(0);
    try {
      await uploadRelease(
        project,
        file,
        version,
        stable ? "stable" : "",
        commit || undefined,
        { proof, onProgress: setProgress },
      );
      done();
    } catch (e) {
      setError(e instanceof Error ? e.message : "发布失败。");
    } finally {
      setBusy(false);
      setProof("");
    }
  };
  return (
    <form onSubmit={submit} className="form-stack">
      <p className="muted">
        {staticProject
          ? "上传构建产物的 ZIP 或 tar.gz，解压时保留包内目录结构。压缩包最大 256 MiB。"
          : "镜像需要先推送到项目仓库。上传由 ctl package 生成的标准包。"}
      </p>
      <label>
        版本号
        <input
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          placeholder="v1.0.0"
          required
          disabled={busy}
        />
      </label>
      <label>
        {staticProject ? "静态文件压缩包" : "标准发布包"}
        <input
          type="file"
          accept={staticProject ? ".zip,.tar.gz,.gz" : ".tar.gz,.gz"}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          required
          disabled={busy}
        />
      </label>
      {staticProject && (
        <label>
          来源提交（可选）
          <input
            value={commit}
            onChange={(e) => setCommit(e.target.value)}
            placeholder="Git commit hash"
            disabled={busy}
          />
        </label>
      )}
      <label className="checkbox">
        <input
          type="checkbox"
          checked={stable}
          onChange={(e) => setStable(e.target.checked)}
          disabled={busy}
        />
        发布成功后设为 stable
      </label>
      {!staticProject && (
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
              disabled={busy}
            />
          </label>
          <p className="muted small">
            只验证本次镜像，不保存或转交安装主机。托管 Registry 无需填写。
          </p>
        </details>
      )}
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {busy && (
        <p className="muted small" role="status">
          校验、上传并验证发布包… 上传 {progress}%
        </p>
      )}
      <button className="primary" disabled={busy}>
        {busy ? "正在验证并发布…" : "登记版本"}
      </button>
    </form>
  );
}
