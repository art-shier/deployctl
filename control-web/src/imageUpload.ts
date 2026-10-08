import { APIError } from "./api";
import type { ImageTag } from "./imageInventory";

export function uploadArchive(
  path: string,
  file: File,
  onProgress: (percent: number) => void,
  signal: AbortSignal,
): Promise<ImageTag> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal.removeEventListener("abort", abort);
    xhr.open("POST", "/api/v1" + path);
    xhr.timeout = 15 * 60 * 1000;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable)
        onProgress(
          Math.min(100, Math.floor((event.loaded / event.total) * 100)),
        );
    };
    xhr.onload = () => {
      cleanup();
      let data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        reject(new APIError(xhr.status, "服务返回异常，请刷新确认仓库状态。"));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data);
        return;
      }
      if (xhr.status === 401)
        window.dispatchEvent(new Event("ctl-session-expired"));
      reject(new APIError(xhr.status, data.message || "上传失败，请重试。"));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new APIError(0, "连接中断，请刷新确认仓库状态后重试。"));
    };
    xhr.ontimeout = () => {
      cleanup();
      reject(new APIError(0, "上传或入库超时，请刷新确认仓库状态后重试。"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(
        new APIError(0, "上传已取消；若已进入入库阶段，请刷新确认仓库状态。"),
      );
    };
    if (signal.aborted) {
      reject(new APIError(0, "上传已取消。"));
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    xhr.send(file);
  });
}
