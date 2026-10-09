import { useEffect, useId, useRef, useState } from "react";
import { Copy, Download, RefreshCw } from "lucide-react";
import { api } from "./api";
import {
  agentGuide,
  validateAgentAccess,
  type AgentAccess as AgentAccessInfo,
} from "./agentAccess";

function CopyBlock({
  label,
  value,
  rows = 3,
  disabled = false,
}: {
  label: string;
  value: string;
  rows?: number;
  disabled?: boolean;
}) {
  const id = useId();
  const ref = useRef<HTMLTextAreaElement>(null);
  const [status, setStatus] = useState("");
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("unavailable");
      await navigator.clipboard.writeText(value);
      setStatus(`已复制 ${label}`);
    } catch {
      ref.current?.focus();
      ref.current?.select();
      setStatus(
        "浏览器未允许访问剪贴板，文本已选中，请手动复制（Ctrl/Cmd+C）。",
      );
    }
  };
  useEffect(() => setStatus(""), [value]);
  return (
    <div className="agent-copy-block">
      <div className="agent-copy-heading">
        <label htmlFor={id}>{label}</label>
        <button
          onClick={copy}
          disabled={disabled || !value}
          aria-label={`复制 ${label}`}
        >
          <Copy size={14} />
          复制
        </button>
      </div>
      <textarea
        id={id}
        ref={ref}
        readOnly
        value={value}
        rows={rows}
        spellCheck={false}
      />
      {status && (
        <p className="small muted" role="status">
          {status}
        </p>
      )}
    </div>
  );
}

export function AgentAccess() {
  const [info, setInfo] = useState<AgentAccessInfo | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, retry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setInfo(null);
    setError("");
    setLoading(true);
    api<unknown>("/agent-access", { signal: controller.signal })
      .then((raw) => {
        if (!controller.signal.aborted)
          setInfo(validateAgentAccess(raw, location.origin));
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "接入信息加载失败，请重试。",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [revision]);
  const guide = info ? agentGuide(info) : null;
  return (
    <div className="agent-access">
      <div className="page-heading">
        <div>
          <p className="eyebrow">AGENT ACCESS</p>
          <h1>Agent 接入</h1>
          <p className="muted">
            把完整 skill、版本信息和登录步骤交给你的 Agent。
          </p>
        </div>
        <span className="badge">无需 MCP</span>
      </div>
      {loading && (
        <p className="loading" role="status">
          正在加载并校验接入信息…
        </p>
      )}
      {error && (
        <div className="notice error" role="alert">
          <span>{error}</span>
          <button onClick={() => retry((value) => value + 1)}>
            <RefreshCw size={14} />
            重试
          </button>
        </div>
      )}
      <section className="panel">
        <h2>1. 获取完整 skill</h2>
        <p className="muted">
          完整 ZIP 包含 SKILL.md、references 和 assets。下载后先校验
          SHA-256，再解压；单独下载 SKILL.md 仅用于阅读。
        </p>
        {info && guide && (
          <>
            <dl className="agent-metadata">
              <div>
                <dt>当前版本</dt>
                <dd>v{info.version}</dd>
              </div>
              <div>
                <dt>服务地址</dt>
                <dd>{info.server_url}</dd>
              </div>
            </dl>
            <div className="agent-links">
              <a
                className="button primary"
                href={info.skillHref}
                download="team-deploy-skill.zip"
              >
                <Download size={16} />
                下载完整 skill ZIP
              </a>
              <a
                className="button"
                href={info.cliHref}
                download="deployctl.pyz"
              >
                下载独立 CLI
              </a>
              <a href={info.entryHref} target="_blank" rel="noreferrer">
                阅读原始 SKILL.md
              </a>
              <a href={info.release_url} target="_blank" rel="noreferrer">
                官方 v{info.version} 版本说明
              </a>
            </div>
            <CopyBlock
              label="skill ZIP SHA-256"
              value={info.skill_sha256}
              rows={2}
            />
            <CopyBlock label="CLI SHA-256" value={info.cli_sha256} rows={2} />
            <CopyBlock label="原始 SKILL.md URL" value={guide.entry} rows={2} />
          </>
        )}
        <p>
          将 ZIP 中的 <code>team-deploy</code> 目录放入{" "}
          <code>~/.codex/skills/team-deploy</code>，或所用 Agent 的 skills
          目录，保留全部内容。让 Agent 阅读 SKILL.md 和相关 references
          后开始工作。
        </p>
      </section>
      <section className="panel">
        <h2>2. 复制 Agent 交接提示词</h2>
        <p className="muted">
          只包含公开资源、校验值和操作说明。Token 由独立的私有文件提供。
        </p>
        <CopyBlock
          label="Agent 交接提示词"
          value={guide?.prompt || ""}
          rows={12}
          disabled={!info}
        />
      </section>
      {guide && (
        <>
          <section className="panel">
            <h2>3. 准备 CLI</h2>
            <p>
              可直接使用已安装的 <code>ctl</code>，并运行{" "}
              <code>ctl --version</code> 检查版本。完整 skill 中的{" "}
              <code>assets/deployctl.pyz</code> 是不需要访问 GitHub
              的替代方式，需要 Python ≥ 3.10。
            </p>
            <CopyBlock label="离线 CLI 命令" value={guide.bundled} rows={5} />
            <p>
              使用 pyz 时，将下方命令中的 <code>ctl</code> 前缀替换为{" "}
              <code>python3 "$SKILL_DIR/assets/deployctl.pyz"</code>；PowerShell
              使用{" "}
              <code>
                python
                "$env:USERPROFILE/.codex/skills/team-deploy/assets/deployctl.pyz"
              </code>
              。保留已有的 ctl，不要定义覆盖它的函数或别名。
            </p>
            <CopyBlock
              label="Windows PowerShell CLI 命令"
              value={guide.windows}
              rows={7}
            />
            <p>需要访问 GitHub 的官方安装方式，固定为当前服务版本：</p>
            <CopyBlock label="官方安装命令" value={guide.install} rows={4} />
            <p>
              仅官方安装器管理的 ctl 可以执行自更新。直接运行的 pyz
              不使用此命令。
            </p>
            <CopyBlock label="CLI 更新命令" value={guide.update} rows={3} />
            <p>
              skill / pyz 更新：重新下载完整 skill ZIP 或独立 pyz，校验当前
              SHA-256 后替换对应资源。<code>ctl self-update</code> 不会更新
              skill。
            </p>
            <p className="notice">
              这些命令只安装或更新 CLI /
              skill。业务发布、安装和升级需要另外执行对应的项目命令，并确认目标环境。
            </p>
          </section>
          <section className="panel">
            <h2>4. 用私有 Token 文件登录</h2>
            <p>
              先在<a href="#tokens">组凭据</a>
              中管理授权。将凭据保存为已存在、仅本人可读的文件，把下方路径替换为该文件路径。不要把
              Token 写入聊天、命令参数、日志或交接提示词，不要回显文件内容。
            </p>
            <p className="muted">
              默认服务 https://ctl.shier.art 无需配置地址；自定义服务先设置
              server，再登录。下方为 Bash 示例；PowerShell
              可使用上方的直接调用示例。
            </p>
            <p>
              登录前执行 <code>ctl config get server</code>
              ，确认保存的地址与页面显示的目标一致。如果不同，先执行{" "}
              <code>ctl config set server '{info?.server_url}'</code>{" "}
              一次，再使用 Token
              文件登录。默认服务且保存地址一致时无需额外配置。
            </p>
            <CopyBlock label="登录命令" value={guide.login} rows={6} />
            <ul className="agent-roles">
              <li>
                <strong>publisher</strong>
                ：在授权范围内管理项目和环境配置，在允许的项目组内创建项目。
              </li>
              <li>
                <strong>deployer</strong>
                ：只部署已有项目，不创建项目或管理环境配置。
              </li>
              <li>
                <strong>owner</strong>：拥有完整管理权限。
              </li>
            </ul>
            <p className="muted">
              操作仍受凭据的项目组、项目、排除项及环境限制约束。部署命令在已授权的
              Linux 目标机运行。
            </p>
          </section>
          <section className="panel">
            <h2>5. 项目与业务部署示例</h2>
            <p>
              <code>notes</code>、<code>apps</code>、<code>prod</code>{" "}
              都是示例，请替换为实际项目、项目组和环境。创建项目与修改配置需要
              publisher 或 owner
              权限；已有项目的安装、升级与状态检查按凭据范围执行。
            </p>
            <CopyBlock label="业务命令示例" value={guide.sample} rows={12} />
          </section>
        </>
      )}
    </div>
  );
}
