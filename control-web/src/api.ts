export class APIError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, {
      ...options,
      credentials: "same-origin",
      headers:
        options.body instanceof FormData
          ? options.headers
          : { "Content-Type": "application/json", ...options.headers },
    });
  } catch {
    throw new APIError(0, "无法连接管理服务，请稍后重试。");
  }
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("ctl-session-expired"));
    let message = "操作失败，请稍后重试。";
    try {
      message = (await response.json()).message || message;
    } catch {}
    throw new APIError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const send = (method: string, body: unknown): RequestInit => ({
  method,
  body: JSON.stringify(body),
});
export type DeploymentType = "docker" | "static";
export interface Project {
  deployment_type?: DeploymentType;
  slug: string;
  group: string;
  name: string;
  description: string;
  repository: string;
  image_repository: string;
  default_environment: string;
  created_at: string;
}
export interface Group {
  slug: string;
  name: string;
  description: string;
  created_at: string;
}
export interface Release {
  deployment_type?: DeploymentType;
  archive_format?: "zip" | "tar.gz";
  expanded_size?: number;
  entry_count?: number;
  id: string;
  project: string;
  version: string;
  image: string;
  sha256: string;
  commit: string;
  size: number;
  status: string;
  created_at: string;
}
export interface MaskedVariable {
  key: string;
  secret: boolean;
  configured: boolean;
  value?: string;
}
export interface Defaults {
  target_dir?: string;
  host_port?: number;
  bind_address?: string;
  memory_limit?: string;
  cpus?: number;
}
export interface Environment {
  id: string;
  environment: string;
  revision: number;
  target_version?: string;
  runtime_env: MaskedVariable[];
  install_params: MaskedVariable[];
  deployment_defaults?: Defaults;
  created_at: string;
  inherited_runtime_env?: MaskedVariable[];
  inherited_install_params?: MaskedVariable[];
  inherited_deployment_defaults?: Defaults;
  group_source?: { slug: string; id: string; revision: number } | null;
}
export interface Token {
  id: string;
  name: string;
  role: string;
  project: string;
  environments: string[];
  projects?: string[];
  groups?: string[];
  excluded_projects?: string[];
  token_readable?: boolean;
  expires_at: string;
  revoked: boolean;
  created_at: string;
}
export interface Receipt {
  id: string;
  host_id: string;
  project: string;
  environment: string;
  release_id: string;
  configuration_revision: string;
  success: boolean;
  cli_version: string;
  created_at: string;
}
export interface Audit {
  id: string;
  actor: string;
  action: string;
  project: string;
  environment: string;
  keys?: string[];
  created_at: string;
}
