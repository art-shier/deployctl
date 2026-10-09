import { describe, expect, it } from "vitest";
import { agentGuide, validateAgentAccess, shellQuote } from "./agentAccess";

const base = {
  version: "1.12.0",
  server_url: "https://ctl.shier.art",
  skill_name: "team-deploy",
  skill_url: "/agent/team-deploy-skill.zip",
  skill_sha256: "a".repeat(64),
  cli_url: "/agent/deployctl.pyz",
  cli_sha256: "b".repeat(64),
  entry_url: "/agent/SKILL.md",
  release_url: "https://github.com/art-shier/deployctl/releases/tag/v1.12.0",
};
const browserOrigin = "http://127.0.0.1:5173";

describe("validated Agent access guides", () => {
  it("uses same-origin download links and canonical service links in the handing prompt", () => {
    const info = validateAgentAccess(
      { ...base, server_url: "https://deploy.example.com:8443/" },
      browserOrigin,
    );
    const guide = agentGuide(info);
    expect(info.skillHref).toBe(
      "http://127.0.0.1:5173/agent/team-deploy-skill.zip",
    );
    expect(guide.prompt).toContain(
      "https://deploy.example.com:8443/agent/team-deploy-skill.zip",
    );
    expect(guide.login).toContain(
      "ctl config set server 'https://deploy.example.com:8443'",
    );
    expect(guide.login).toContain('ctl login --token-file "$TOKEN_FILE"');
  });
  it("omits address configuration for the default service and pins official installation", () => {
    const guide = agentGuide(validateAgentAccess(base, browserOrigin));
    expect(guide.login).not.toContain("config set server");
    expect(guide.install).toContain("/v1.12.0/install.sh");
    expect(guide.install).toContain("--version v1.12.0");
    expect(guide.bundled).toContain("assets/deployctl.pyz");
    expect(guide.bundled).not.toContain("github");
  });
  it("quotes apostrophes as literal shell data", () => {
    expect(shellQuote("a'b$(command)")).toBe("'a'\\''b$(command)'");
  });
  it("keeps installed ctl intact and distinguishes bundled skill updates", () => {
    const guide = agentGuide(validateAgentAccess(base, browserOrigin));
    expect(guide.prompt).not.toContain("ctl()");
    expect(guide.bundled).not.toContain("ctl()");
    expect(guide.bundled).toContain(
      'python3 "$SKILL_DIR/assets/deployctl.pyz" --version',
    );
    expect(guide.prompt).toContain("将后续命令中的 ctl 前缀替换");
    expect(guide.update).toContain("仅适用于官方安装器管理的 ctl");
    expect(guide.prompt).toContain("skill / pyz 更新：重新下载");
    expect(guide.prompt).toContain("不会更新 skill");
  });
  it("provides PowerShell direct pyz version and private-file login commands", () => {
    const guide = agentGuide(
      validateAgentAccess(
        { ...base, server_url: "https://deploy.example.com:8443" },
        browserOrigin,
      ),
    );
    expect(guide.windows).toContain(
      'python "$env:USERPROFILE/.codex/skills/team-deploy/assets/deployctl.pyz" --version',
    );
    expect(guide.windows).toContain(
      "config set server 'https://deploy.example.com:8443'",
    );
    expect(guide.windows).toContain('login --token-file "$TokenFile"');
    expect(guide.windows).not.toContain("self-update");
    expect(guide.prompt).toContain(guide.windows);
  });
  it("checks the saved server before login and explains how to correct a previous target", () => {
    const guide = agentGuide(validateAgentAccess(base, browserOrigin));
    expect(guide.login).toContain("ctl config get server");
    expect(guide.login.indexOf("config get server")).toBeLessThan(
      guide.login.indexOf("login --token-file"),
    );
    expect(guide.login).not.toContain("config set server");
    expect(guide.prompt).toContain("已保存的服务地址与页面显示的目标不同");
    expect(guide.prompt).toContain(
      "ctl config set server 'https://ctl.shier.art'",
    );
    expect(guide.windows.indexOf("config get server")).toBeGreaterThan(-1);
    expect(guide.windows.indexOf("config get server")).toBeLessThan(
      guide.windows.indexOf("login --token-file"),
    );
  });
  it("accepts HTTP only for loopback services", () => {
    for (const server_url of [
      "http://localhost:8086",
      "http://127.0.0.1:8086",
      "http://[::1]:8086",
    ])
      expect(
        validateAgentAccess({ ...base, server_url }, browserOrigin).server_url,
      ).toBe(server_url);
    expect(() =>
      validateAgentAccess(
        { ...base, server_url: "http://example.com" },
        browserOrigin,
      ),
    ).toThrow();
  });
  it("normalizes configured uppercase hosts and default ports and accepts HTTPS IPv6", () => {
    for (const [server_url, expected] of [
      ["https://CTL.EXAMPLE.COM:443", "https://ctl.example.com"],
      ["https://[2001:db8::1]", "https://[2001:db8::1]"],
      ["https://[2001:DB8::1]:443/", "https://[2001:db8::1]"],
    ])
      expect(
        validateAgentAccess({ ...base, server_url }, browserOrigin).server_url,
      ).toBe(expected);
  });
  it("rejects command injection, credential-bearing origins and replaced artifacts", () => {
    for (const patch of [
      { version: "1.12.0; echo unsafe" },
      { version: "$(command)" },
      { server_url: "https://user:secret@ctl.shier.art" },
      { server_url: "https://ctl.shier.art/?token=secret" },
      { server_url: "https://ctl.shier.art/path" },
      { server_url: "https://ctl.shier.art/.." },
      { server_url: "https:///ctl.shier.art" },
      { server_url: "https:ctl.shier.art" },
      { server_url: "http://[2001:db8::1]" },
      { server_url: "javascript:alert(1)" },
      { server_url: "https://exa'mple.com" },
      { server_url: "https://ctl.shier.art\n" },
      { skill_url: "https://evil.example/team-deploy-skill.zip" },
      { skill_url: "/agent/../tokens" },
      { cli_url: "//evil.example/deployctl.pyz" },
      { entry_url: "/api/v1/tokens" },
      { skill_sha256: "$(command)" },
      { cli_sha256: "b".repeat(63) },
      {
        release_url:
          "https://github.com/art-shier/deployctl/releases/tag/v1.11.0",
      },
      { skill_name: "other-skill" },
      { token: "ctl_private_sentinel" },
    ])
      expect(() =>
        validateAgentAccess({ ...base, ...patch }, browserOrigin),
      ).toThrow();
  });
});
