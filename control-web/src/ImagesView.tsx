import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Search,
  Terminal,
  Trash2,
  Upload,
} from "lucide-react";
import { api, type Project } from "./api";
import {
  filterImages,
  groupImages,
  type ImageGroup,
  type ImageTag,
} from "./imageInventory";
import { uploadArchive } from "./imageUpload";

type Dialog = React.ComponentType<{
  title: string;
  close: () => void;
  children: React.ReactNode;
}>;
type Details = {
  digest: string;
  media_type: string;
  size_bytes: number;
  platforms: string[];
};
const message = (err: unknown) =>
  err instanceof Error ? err.message : "操作失败，请重试。";
const bytes = (value: number) =>
  value < 1024 * 1024
    ? `${(value / 1024).toFixed(1)} KiB`
    : `${(value / 1024 / 1024).toFixed(1)} MiB`;
function Notice({ text, error = false }: { text: string; error?: boolean }) {
  return text ? (
    <div
      className={"notice " + (error ? "error" : "success")}
      role={error ? "alert" : "status"}
    >
      {text}
    </div>
  ) : null;
}

export function ImagesView({
  project,
  Dialog,
}: {
  project: Project;
  Dialog: Dialog;
}) {
  const [images, setImages] = useState<ImageTag[]>([]),
    [capabilities, setCapabilities] = useState<{
      managed: boolean;
      max_archive_bytes: number;
    } | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [actionError, setActionError] = useState(""),
    [notice, setNotice] = useState(""),
    [proof, setProof] = useState(""),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1),
    [upload, setUpload] = useState(false),
    [push, setPush] = useState(false),
    [removing, setRemoving] = useState<ImageGroup | null>(null),
    [deleting, setDeleting] = useState(false),
    [selected, setSelected] = useState<ImageGroup | null>(null),
    [details, setDetails] = useState<Details | null>(null),
    [detailError, setDetailError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const load = async (token = "") => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError("");
    setProof("");
    try {
      const result = await api<ImageTag[]>(`/projects/${project.slug}/images`, {
        signal: current.signal,
        headers: token ? { "X-Registry-Verification-Token": token } : undefined,
      });
      if (!current.signal.aborted) {
        setImages(result);
        setPage(1);
      }
    } catch (err) {
      if (!current.signal.aborted) setError(message(err));
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  };
  useEffect(() => {
    const current = new AbortController();
    void load();
    api<{ managed: boolean; max_archive_bytes: number }>(
      `/projects/${project.slug}/images/capabilities`,
      { signal: current.signal },
    )
      .then(setCapabilities)
      .catch(() => {});
    return () => {
      current.abort();
      controller.current?.abort();
    };
  }, [project.slug]);
  useEffect(() => setPage(1), [query]);
  const groups = useMemo(() => groupImages(images), [images]);
  const filtered = useMemo(() => filterImages(groups, query), [groups, query]);
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  const shown = filtered.slice(
    (Math.min(page, pages) - 1) * 20,
    Math.min(page, pages) * 20,
  );
  const remove = async () => {
    if (!removing) return;
    setDeleting(true);
    setActionError("");
    try {
      await api(`/projects/${project.slug}/images/${removing.digest}`, {
        method: "DELETE",
      });
      setRemoving(null);
      setNotice("镜像及其关联标签已删除。存储层空间在维护回收后释放。");
      await load();
    } catch (err) {
      setActionError(message(err));
      setRemoving(null);
    } finally {
      setDeleting(false);
    }
  };
  useEffect(() => {
    if (!selected) return;
    const current = new AbortController();
    setDetails(null);
    setDetailError("");
    api<Details>(`/projects/${project.slug}/images/${selected.digest}`, {
      signal: current.signal,
      headers: proof ? { "X-Registry-Verification-Token": proof } : undefined,
    })
      .then(setDetails)
      .catch((err) => {
        if (!current.signal.aborted) setDetailError(message(err));
      });
    setProof("");
    return () => current.abort();
  }, [selected, project.slug]);
  return (
    <section className="image-management">
      <div className="section-heading">
        <div>
          <h2>镜像管理</h2>
          <p className="muted small">{project.image_repository}</p>
        </div>
        <div className="image-actions">
          <button onClick={() => void load(proof)} disabled={loading}>
            <RefreshCw size={16} />
            刷新
          </button>
          {capabilities?.managed && (
            <>
              <button onClick={() => setPush(true)}>
                <Terminal size={16} />
                手动推送
              </button>
              <button className="primary" onClick={() => setUpload(true)}>
                <Upload size={16} />
                上传镜像
              </button>
            </>
          )}
        </div>
      </div>
      <div className="image-stats" aria-label="仓库统计">
        <div>
          <span>镜像数</span>
          <strong data-testid="image-count">
            {loading || error ? "—" : groups.length}
          </strong>
          <small>按 Digest 去重</small>
        </div>
        <div>
          <span>标签数</span>
          <strong data-testid="image-tag-count">
            {loading || error ? "—" : images.length}
          </strong>
          <small>同一镜像可有多个标签</small>
        </div>
        <div>
          <span>未关联版本</span>
          <strong>
            {loading || error
              ? "—"
              : groups.filter((image) => !image.versions.length).length}
          </strong>
          <small>登记部署包后才可安装</small>
        </div>
      </div>
      {capabilities && !capabilities.managed && (
        <p className="muted small">
          当前是外部镜像仓库，上传与删除请在该仓库操作。推送后刷新，再到“版本”登记部署包。
        </p>
      )}
      <details className="registry-proof">
        <summary>外部私有仓库验证</summary>
        <label>
          短期 pull Token
          <input
            type="password"
            autoComplete="off"
            maxLength={12288}
            value={proof}
            onChange={(event) => setProof(event.target.value)}
          />
        </label>
        <p className="muted small">仅用于下一次刷新或详情读取，不保存。</p>
      </details>
      <Notice text={notice} />
      <Notice text={error || actionError} error />
      <label className="image-search">
        <Search size={17} />
        <input
          aria-label="搜索镜像"
          placeholder="搜索标签、Digest 或关联版本"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {loading ? (
        <p className="loading" role="status">
          正在读取仓库镜像…
        </p>
      ) : error ? (
        <button onClick={() => void load(proof)}>重新加载</button>
      ) : shown.length ? (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>标签</th>
                  <th>Digest / 类型</th>
                  <th>关联版本</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((image) => (
                  <tr key={image.digest}>
                    <td>
                      <div className="image-tags">
                        {image.tags.map((tag) => (
                          <span className="badge mono" key={tag}>
                            {tag}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <button
                        className="link-button mono truncate"
                        title={image.digest}
                        onClick={() => setSelected(image)}
                      >
                        {image.digest}
                      </button>
                      <div className="muted small">
                        {image.media_type.includes("index") ||
                        image.media_type.includes("manifest.list")
                          ? "多架构镜像"
                          : "镜像"}
                      </div>
                    </td>
                    <td>
                      {image.versions.length ? (
                        image.versions.join(", ")
                      ) : (
                        <span className="badge">未关联版本</span>
                      )}
                    </td>
                    <td>
                      <div className="image-row-actions">
                        <button onClick={() => setSelected(image)}>详情</button>
                        {capabilities?.managed && (
                          <button
                            className="icon-button danger"
                            aria-label={`删除镜像 ${image.tags.join(", ")}`}
                            title={
                              image.versions.length
                                ? "发布及回滚版本引用的镜像受到保护"
                                : "删除镜像"
                            }
                            disabled={!!image.versions.length}
                            onClick={() => setRemoving(image)}
                          >
                            <Trash2 size={16} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="image-pagination">
            <span className="muted small">
              匹配 {filtered.length} 个镜像 · 第 {Math.min(page, pages)} /{" "}
              {pages} 页
            </span>
            <div>
              <button
                aria-label="上一页"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <button
                aria-label="下一页"
                disabled={page >= pages}
                onClick={() => setPage(page + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        </>
      ) : (
        <div className="empty">
          <Box size={30} />
          <h3>{query ? "没有匹配的镜像" : "仓库还没有镜像"}</h3>
          <p>
            {query
              ? "尝试其他标签、Digest 或版本号。"
              : "通过流水线、手动推送或上传镜像后，在这里统一管理。"}
          </p>
        </div>
      )}
      <p className="muted small">
        发布和回滚引用的镜像受到保护。删除按 Digest
        执行，所有关联标签会一起删除；磁盘空间需要维护回收后释放。
      </p>
      {removing && (
        <Dialog
          title="删除镜像"
          close={() => {
            if (!deleting) setRemoving(null);
          }}
        >
          <div className="form-stack">
            <p>将删除该镜像及以下全部标签，此操作无法撤销。</p>
            <div className="image-tags">
              {removing.tags.map((tag) => (
                <span className="badge mono" key={tag}>
                  {tag}
                </span>
              ))}
            </div>
            <code className="code-block">{removing.digest}</code>
            <p className="muted small">
              服务端会再次检查所有项目的版本引用及多架构镜像依赖。
            </p>
            <button
              className="danger"
              disabled={deleting}
              onClick={() => void remove()}
            >
              {deleting ? "正在检查并删除…" : "确认删除镜像"}
            </button>
          </div>
        </Dialog>
      )}
      {selected && (
        <Dialog title="镜像详情" close={() => setSelected(null)}>
          <div className="detail-stack">
            <label>
              完整地址
              <code className="code-block">
                {project.image_repository}@{selected.digest}
              </code>
            </label>
            <label>
              标签
              <div className="image-tags">
                {selected.tags.map((tag) => (
                  <span className="badge mono" key={tag}>
                    {tag}
                  </span>
                ))}
              </div>
            </label>
            <label>
              关联版本
              <span>{selected.versions.join(", ") || "尚未登记部署包"}</span>
            </label>
            <Notice text={detailError} error />
            {details ? (
              <>
                <label>
                  架构<span>{details.platforms.join(", ")}</span>
                </label>
                <label>
                  内容大小（压缩）<span>{bytes(details.size_bytes)}</span>
                </label>
                <p className="muted small">
                  大小按该镜像的去重内容计算，不等于整个仓库实际占用空间。
                </p>
              </>
            ) : !detailError ? (
              <p role="status">正在读取镜像详情…</p>
            ) : null}
          </div>
        </Dialog>
      )}
      {push && (
        <Dialog title="手动推送镜像" close={() => setPush(false)}>
          <div className="detail-stack">
            <p>
              使用本项目的 publisher
              凭据登录，再为本地镜像设置仓库地址并推送。建议使用新的版本标签。
            </p>
            <code className="code-block">{`docker login ${project.image_repository.split("/")[0]} --username ctl\ndocker tag your-image:local ${project.image_repository}:your-tag\ndocker push ${project.image_repository}:your-tag`}</code>
            <p className="muted small">
              登录时交互输入凭据。推送完成后刷新列表，到“版本”关联部署包。
            </p>
          </div>
        </Dialog>
      )}
      {upload && (
        <UploadDialog
          project={project}
          Dialog={Dialog}
          limit={capabilities?.max_archive_bytes ?? 2 * 1024 * 1024 * 1024}
          close={() => setUpload(false)}
          done={() => {
            setUpload(false);
            setNotice("镜像已入库。请到“版本”登记对应部署包后安装。");
            void load();
          }}
        />
      )}
    </section>
  );
}

function UploadDialog({
  project,
  Dialog,
  limit,
  close,
  done,
}: {
  project: Project;
  Dialog: Dialog;
  limit: number;
  close: () => void;
  done: () => void;
}) {
  const [tag, setTag] = useState(""),
    [file, setFile] = useState<File | null>(null),
    [busy, setBusy] = useState(false),
    [percent, setPercent] = useState(0),
    [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  const dismiss = () => {
    if (busy) {
      if (!confirm("取消当前上传？若已开始入库，请刷新确认仓库状态。")) return;
      abort.current?.abort();
    }
    close();
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setError("");
    if (file.size > limit) {
      setError("镜像文件不能超过 2 GiB，请使用 docker push 上传更大的镜像。");
      return;
    }
    if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(tag)) {
      setError("标签只能包含字母、数字、下划线、点和连字符，最多 128 字符。");
      return;
    }
    const current = new AbortController();
    abort.current = current;
    setBusy(true);
    setPercent(0);
    try {
      await uploadArchive(
        `/projects/${project.slug}/images/upload?tag=${encodeURIComponent(tag)}`,
        file,
        setPercent,
        current.signal,
      );
      done();
    } catch (err) {
      if (!current.signal.aborted) setError(message(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="上传镜像" close={dismiss}>
      <form className="form-stack" onSubmit={submit}>
        <p className="muted">
          选择 docker save 导出的单镜像 .tar
          文件，上传到当前项目仓库。不会覆盖已有不同镜像的标签。
        </p>
        <code className="code-block">
          docker save --output image.tar your-image:local
        </code>
        <label>
          目标标签
          <input
            required
            maxLength={128}
            placeholder="例如 v0.3.0 或 manual-test"
            value={tag}
            disabled={busy}
            onChange={(event) => setTag(event.target.value)}
          />
        </label>
        <label>
          镜像 tar 文件
          <input
            required
            type="file"
            accept=".tar,application/x-tar"
            disabled={busy}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        {file && (
          <p className="muted small">
            {file.name} · {bytes(file.size)}
          </p>
        )}
        {busy && (
          <div className="upload-progress" role="status">
            <progress aria-label="镜像上传进度" max={100} value={percent} />
            <span>
              {percent < 100
                ? `正在上传 ${percent}%`
                : "上传完成，正在校验并写入仓库…"}
            </span>
          </div>
        )}
        <Notice text={error} error />
        <p className="muted small">
          最大 2 GiB，上传及入库最长 15 分钟。多架构镜像或更大的镜像请使用
          docker push。
        </p>
        <div className="modal-actions">
          <button type="button" onClick={dismiss}>
            {busy ? "取消上传" : "取消"}
          </button>
          <button className="primary" disabled={busy}>
            {busy ? "正在处理…" : "上传并入库"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
