export interface AgentAccess {
  version: string;
  server_url: string;
  skill_name: string;
  skill_url: string;
  skill_sha256: string;
  cli_url: string;
  cli_sha256: string;
  entry_url: string;
  release_url: string;
  skillHref: string;
  cliHref: string;
  entryHref: string;
}

const fields = [
  "version",
  "server_url",
  "skill_name",
  "skill_url",
  "skill_sha256",
  "cli_url",
  "cli_sha256",
  "entry_url",
  "release_url",
] as const;
const invalid = () =>
  new Error("Agent 接入信息校验失败，请重试或联系服务管理员。");

export function validateAgentAccess(
  raw: unknown,
  browserOrigin: string,
): AgentAccess {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw invalid();
  const data = raw as Record<string, unknown>;
  if (
    Object.keys(data).length !== fields.length ||
    fields.some((key) => typeof data[key] !== "string")
  )
    throw invalid();
  const info = data as unknown as Omit<
    AgentAccess,
    "skillHref" | "cliHref" | "entryHref"
  >;
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(info.version))
    throw invalid();
  if (
    info.skill_name !== "team-deploy" ||
    info.skill_url !== "/agent/team-deploy-skill.zip" ||
    info.cli_url !== "/agent/deployctl.pyz" ||
    info.entry_url !== "/agent/SKILL.md"
  )
    throw invalid();
  if (
    ![info.skill_sha256, info.cli_sha256].every((value) =>
      /^[a-f0-9]{64}$/.test(value),
    )
  )
    throw invalid();
  if (
    info.release_url !==
    `https://github.com/art-shier/deployctl/releases/tag/v${info.version}`
  )
    throw invalid();
  if (
    !/^https?:\/\/(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:]+\])(?::[0-9]+)?\/?$/.test(
      info.server_url,
    )
  )
    throw invalid();
  let origin: URL;
  try {
    origin = new URL(info.server_url);
  } catch {
    throw invalid();
  }
  if (
    origin.username ||
    origin.password ||
    origin.search ||
    origin.hash ||
    origin.pathname !== "/" ||
    !/^(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:]+\])$/.test(origin.hostname)
  )
    throw invalid();
  if (
    origin.protocol !== "https:" &&
    !(
      origin.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname)
    )
  )
    throw invalid();
  return {
    ...info,
    server_url: origin.origin,
    skillHref: new URL(info.skill_url, browserOrigin).href,
    cliHref: new URL(info.cli_url, browserOrigin).href,
    entryHref: new URL(info.entry_url, browserOrigin).href,
  };
}

export function shellQuote(value: string): string {
  return "'" + value.replaceAll("'", "'\\''") + "'";
}

export function agentGuide(info: AgentAccess) {
  const version = `v${info.version}`;
  const skill = info.server_url + info.skill_url;
  const cli = info.server_url + info.cli_url;
  const entry = info.server_url + info.entry_url;
  const login = `ctl config get server
${info.server_url === "https://ctl.shier.art" ? "# 默认服务且保存地址一致时无需配置地址\n" : `ctl config set server ${shellQuote(info.server_url)}\n`}# 将路径改为已存在、仅本人可读的凭据文件；不要粘贴 Token
TOKEN_FILE="$HOME/.config/ctl/agent.token"
ctl login --token-file "$TOKEN_FILE"
ctl whoami`;
  const install = `curl --fail --silent --show-error ${shellQuote(`https://raw.githubusercontent.com/art-shier/deployctl/${version}/install.sh`)} \\
  | bash -s -- --user --version ${version}
ctl --version`;
  const update = `# 仅适用于官方安装器管理的 ctl
ctl self-update --version ${version}
ctl --version`;
  const bundled = `# Python >= 3.10；已解压完整 skill，无需访问 GitHub
SKILL_DIR="$HOME/.codex/skills/team-deploy"
python3 "$SKILL_DIR/assets/deployctl.pyz" --version`;
  const windowsCli =
    'python "$env:USERPROFILE/.codex/skills/team-deploy/assets/deployctl.pyz"';
  const windows = `# Windows PowerShell；Python >= 3.10
${windowsCli} --version
${windowsCli} config get server
${info.server_url === "https://ctl.shier.art" ? "# 默认服务无需配置地址" : `${windowsCli} config set server ${shellQuote(info.server_url)}`}
# 将路径改为已存在、仅本人可读的凭据文件
$TokenFile = "$env:USERPROFILE/.config/ctl/agent.token"
${windowsCli} login --token-file "$TokenFile"
${windowsCli} whoami`;
  const sample = `# notes / apps / prod 都是示例，请替换为实际授权范围
ctl project list
ctl project show notes
# publisher：创建项目、管理环境配置
ctl project create notes --name 'Notes' --group apps --default-env prod
ctl project-config get notes --prod
ctl project-config set notes APP_MODE production --prod
# 在已授权的 Linux 目标机上部署已有项目
ctl install notes --prod
ctl upgrade notes --prod
ctl status notes --prod`;
  const prompt = `请使用 team-deploy skill 接入部署控制台，不需要 MCP。
服务地址：${info.server_url}
当前版本：${version}
完整 skill ZIP：${skill}
skill ZIP SHA-256：${info.skill_sha256}
原始入口 SKILL.md：${entry}
独立 CLI：${cli}
CLI SHA-256：${info.cli_sha256}
官方版本说明：${info.release_url}

下载完整 ZIP，校验 SHA-256 后将 team-deploy 目录放到 ~/.codex/skills/team-deploy（或所用 Agent 的 skills 目录），保留 assets、references 等完整内容。只下载 SKILL.md 不足以安装 skill。阅读其中的 SKILL.md 和相关 references，再执行操作。
优先使用已安装的 ctl；也可用完整 skill 中 assets/deployctl.pyz（Python >= 3.10），这条路径无需访问 GitHub。安装后检查 ctl --version。

${bundled}

使用 pyz 时，将后续命令中的 ctl 前缀替换为 python3 "$SKILL_DIR/assets/deployctl.pyz"；PowerShell 使用 python "$env:USERPROFILE/.codex/skills/team-deploy/assets/deployctl.pyz"。不要定义函数或别名来覆盖已有 ctl。
Windows PowerShell 的直接调用与私有文件登录示例：
${windows}

登录前检查 ctl config get server。如果已保存的服务地址与页面显示的目标不同，先执行 ctl config set server ${shellQuote(info.server_url)} 一次，再进行 Token 文件登录。默认服务且保存地址一致时无需额外配置。
使用已有且仅本人可读的 Token 文件登录。不要将真实 Token、密码或私钥写入对话、命令参数、日志或交接文本；不要读取或回显文件内容。
${login}

publisher 只能在授权范围内管理项目与环境配置，并在允许的项目组内创建项目；deployer 只部署已有项目，不创建项目、不管理配置；owner 拥有完整管理权限。遵守凭据的项目组、项目、排除项和环境限制。

官方安装（需要访问 GitHub，固定版本）：
${install}

CLI 自更新（仅官方安装器管理的 ctl）：
${update}
不要对直接运行的 pyz 使用 self-update。skill / pyz 更新：重新下载完整 skill ZIP 或独立 pyz，校验当前 SHA-256 后替换对应资源。ctl self-update 不会更新 skill。
以上只安装或更新 CLI / skill，不会发布或升级业务。业务部署需要另行确认目标项目、环境、版本和目标机。

命令示例（notes / apps / prod 是占位示例，不代表已经获得授权）：
${sample}`;
  return {
    prompt,
    login,
    install,
    update,
    bundled,
    windows,
    sample,
    skill,
    cli,
    entry,
  };
}
