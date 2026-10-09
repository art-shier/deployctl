import { test, expect, type Page } from "@playwright/test";

const metadata = {
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
async function fixture(
  page: Page,
  response: unknown = metadata,
  failFirst = false,
) {
  const paths: string[] = [];
  let failing = failFirst;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    paths.push(path);
    if (path === "/api/v1/agent-access") {
      if (failing) {
        await route.fulfill({
          status: 503,
          json: { message: "元数据暂时不可用" },
        });
        return;
      }
      await route.fulfill({ json: response });
      return;
    }
    await route.fulfill({
      json: path === "/api/v1/me" ? { role: "owner" } : [],
    });
  });
  return Object.assign(paths, {
    recover: () => {
      failing = false;
    },
  });
}

test("sidebar and direct Agent link preserve canonical prompt and same-origin downloads", async ({
  page,
}) => {
  const paths = await fixture(page, {
    ...metadata,
    server_url: "https://deploy.example.com:8443",
  });
  await page.goto("/#groups");
  await page.getByRole("link", { name: "Agent 接入", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Agent 接入", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "下载完整 skill ZIP" }),
  ).toHaveAttribute("href", /\/agent\/team-deploy-skill.zip$/);
  const origin = new URL(page.url()).origin;
  await expect(
    page.getByRole("link", { name: "下载完整 skill ZIP" }),
  ).toHaveAttribute("href", origin + metadata.skill_url);
  await expect(
    page.getByLabel("Agent 交接提示词", { exact: true }),
  ).toHaveValue(
    /https:\/\/deploy.example.com:8443\/agent\/team-deploy-skill.zip/,
  );
  await expect(page.getByLabel("登录命令", { exact: true })).toHaveValue(
    /ctl config set server 'https:\/\/deploy.example.com:8443'/,
  );
  await page.reload();
  await expect(
    page.getByLabel("Agent 交接提示词", { exact: true }),
  ).toHaveValue(/ctl login --token-file "\$TOKEN_FILE"/);
  expect(paths.some((path) => /tokens|secret/.test(path))).toBe(false);
});

test("loading disables actions and failed metadata can be retried", async ({
  page,
}) => {
  const paths = await fixture(page, metadata, true);
  await page.goto("/#agent");
  await expect(page.getByRole("alert")).toContainText("元数据暂时不可用");
  await expect(
    page.getByRole("button", { name: "复制 Agent 交接提示词", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("link", { name: "下载完整 skill ZIP" }),
  ).toHaveCount(0);
  paths.recover();
  await page.getByRole("button", { name: "重试" }).click();
  await expect(
    page.getByRole("button", { name: "复制 Agent 交接提示词", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("登录命令", { exact: true })).not.toHaveValue(
    /config set server/,
  );
});

test("unsafe metadata never becomes executable instructions or download links", async ({
  page,
}) => {
  await fixture(page, {
    ...metadata,
    version: "1.12.0; unsafe_sentinel",
    cli_url: "/api/v1/tokens",
  });
  await page.goto("/#agent");
  await expect(page.getByRole("alert")).toContainText("校验失败");
  await expect(
    page.getByRole("button", { name: "复制 Agent 交接提示词", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("link", { name: "下载完整 skill ZIP" }),
  ).toHaveCount(0);
  await expect(
    page.getByLabel("Agent 交接提示词", { exact: true }),
  ).toHaveValue("");
  await expect(page.locator("body")).not.toContainText("unsafe_sentinel");
});

test("pending metadata keeps copy and download actions unavailable", async ({
  page,
}) => {
  await fixture(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/agent-access", async (route) => {
    await pending;
    await route.fulfill({ json: metadata });
  });
  await page.goto("/#agent");
  await expect(page.getByRole("status")).toContainText("正在加载并校验");
  await expect(
    page.getByRole("button", { name: "复制 Agent 交接提示词", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("link", { name: "下载完整 skill ZIP" }),
  ).toHaveCount(0);
  release();
  await expect(
    page.getByRole("button", { name: "复制 Agent 交接提示词", exact: true }),
  ).toBeEnabled();
});

test("missing clipboard API leaves instructions selectable", async ({
  page,
}) => {
  await fixture(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    });
  });
  await page.goto("/#agent");
  await page
    .getByRole("button", { name: "复制 Agent 交接提示词", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Agent 交接提示词", exact: true }),
  ).toBeFocused();
  await expect(
    page.getByRole("status").filter({ hasText: "请手动复制" }),
  ).toBeVisible();
});

test("clipboard success copies public instructions only", async ({ page }) => {
  const paths = await fixture(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async (text: string) => {
          (window as unknown as { copied: string }).copied = text;
        },
      },
      configurable: true,
    });
  });
  await page.goto("/#agent");
  await page
    .getByRole("button", { name: "复制 Agent 交接提示词", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "已复制 Agent 交接提示词" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => (window as unknown as { copied: string }).copied),
  ).toContain(metadata.skill_sha256);
  expect(paths.some((path) => /tokens|secret/.test(path))).toBe(false);
});

test("clipboard denial selects text for manual copying", async ({ page }) => {
  await fixture(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async () => {
          throw new Error("denied");
        },
      },
      configurable: true,
    });
  });
  await page.goto("/#agent");
  await page
    .getByRole("button", { name: "复制 Agent 交接提示词", exact: true })
    .click();
  const prompt = page.getByLabel("Agent 交接提示词", { exact: true });
  await expect(prompt).toBeFocused();
  await expect(
    page.getByRole("status").filter({ hasText: "请手动复制" }),
  ).toBeVisible();
  expect(
    await prompt.evaluate(
      (node: HTMLTextAreaElement) => node.selectionEnd - node.selectionStart,
    ),
  ).toBeGreaterThan(100);
});

test("mobile Agent page fits screen and explains installation, roles and example scope", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await fixture(page);
  await page.goto("/#agent");
  await expect(
    page.getByRole("heading", { name: "Agent 接入", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("无需 MCP", { exact: true })).toBeVisible();
  await expect(page.getByLabel("离线 CLI 命令", { exact: true })).toHaveValue(
    /assets\/deployctl.pyz/,
  );
  await expect(
    page.getByLabel("Windows PowerShell CLI 命令", { exact: true }),
  ).toHaveValue(
    /python "\$env:USERPROFILE\/\.codex\/skills\/team-deploy\/assets\/deployctl.pyz" --version/,
  );
  expect(
    await page.getByLabel("Agent 交接提示词", { exact: true }).inputValue(),
  ).not.toContain("ctl()");
  await expect(
    page.getByText(
      "仅官方安装器管理的 ctl 可以执行自更新。直接运行的 pyz 不使用此命令。",
    ),
  ).toBeVisible();
  await expect(page.getByLabel("官方安装命令", { exact: true })).toHaveValue(
    /--version v1.12.0/,
  );
  await expect(page.getByLabel("CLI 更新命令", { exact: true })).toHaveValue(
    /ctl self-update --version v1.12.0/,
  );
  await expect(page.getByLabel("业务命令示例", { exact: true })).toHaveValue(
    /notes \/ apps \/ prod 都是示例/,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
