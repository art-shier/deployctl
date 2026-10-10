import { sha256 } from "@noble/hashes/sha2.js";
import { APIError, type Project, type Release } from "./api";

export async function hashReleaseFile(file: File): Promise<string> {
  const hash = sha256.create();
  for (let offset = 0; offset < file.size; offset += 1024 * 1024)
    hash.update(
      new Uint8Array(
        await file.slice(offset, offset + 1024 * 1024).arrayBuffer(),
      ),
    );
  return Array.from(hash.digest(), (n) => n.toString(16).padStart(2, "0")).join(
    "",
  );
}

export async function prepareReleaseForm(
  project: Project,
  file: File,
  version: string,
  channel: string,
  commit?: string,
): Promise<FormData> {
  const staticProject = project.deployment_type === "static";
  const max = (staticProject ? 256 : 10) * 1024 * 1024;
  if (file.size === 0 || file.size > max)
    throw new Error(
      `发布包必须非空且不能超过 ${staticProject ? 256 : 10} MiB。`,
    );
  if (
    !/^v?\d+\.\d+\.\d+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?$/.test(version) ||
    version.length > 96
  )
    throw new Error("请输入有效的版本号，例如 v1.0.0。");
  if (commit && !/^[a-f0-9]{40,64}$/.test(commit))
    throw new Error("来源提交必须为 Git commit hash。");
  if (channel !== "" && channel !== "stable") throw new Error("发布通道无效。");
  const form = new FormData();
  form.set("package", file);
  form.set("version", version);
  form.set("sha256", await hashReleaseFile(file));
  if (channel) form.set("channel", channel);
  if (commit) form.set("commit", commit);
  return form;
}

export async function uploadRelease(
  project: Project,
  file: File,
  version: string,
  channel: string,
  commit?: string,
  options?: { proof?: string; onProgress?: (percent: number) => void },
): Promise<Release> {
  if (project.deployment_type === "static" && options?.proof)
    throw new Error("静态项目不需要镜像仓库凭据。");
  const form = await prepareReleaseForm(
    project,
    file,
    version,
    channel,
    commit,
  );
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api/v1/projects/${project.slug}/releases`);
    request.withCredentials = true;
    if (options?.proof)
      request.setRequestHeader("X-Registry-Verification-Token", options.proof);
    request.upload.onprogress = (e) => {
      if (e.lengthComputable)
        options?.onProgress?.(Math.round((e.loaded / e.total) * 100));
    };
    request.onerror = () =>
      reject(new APIError(0, "无法连接管理服务，请稍后重试。"));
    request.onabort = () => reject(new APIError(0, "上传已取消。"));
    request.onload = () => {
      if (request.status === 401)
        window.dispatchEvent(new Event("ctl-session-expired"));
      let value: unknown;
      try {
        value = JSON.parse(request.responseText);
      } catch {
        reject(new APIError(request.status, "管理服务返回了无效响应。"));
        return;
      }
      if (request.status < 200 || request.status >= 300) {
        const message =
          typeof value === "object" &&
          value !== null &&
          "message" in value &&
          typeof value.message === "string"
            ? value.message
            : "发布失败，请检查版本、权限和压缩包。";
        reject(new APIError(request.status, message));
        return;
      }
      resolve(value as Release);
    };
    request.send(form);
  });
}
