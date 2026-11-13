import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page, loseResponse = false) {
  const jobs: Record<string, unknown>[] = [];
  const probes: Record<string, unknown>[] = [];
  const submissions: Record<string, unknown>[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    let json: unknown = [];
    if (path === "/me") json = { role: "owner" };
    if (path === "/node-installations/probe") {
      probes.push(request.postDataJSON());
      json = { fingerprint: "SHA256:verified-key" };
    }
    if (path === "/node-installations") {
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        submissions.push(body);
        const job = {
          id: body.id,
          host: body.host,
          port: body.port,
          user: body.user,
          status: "running",
          stage: "docker",
          error_code: "",
          node_id: "",
          created_at: new Date().toISOString(),
          expires_at: new Date(Date.now() + 1500000).toISOString(),
        };
        jobs.push(job);
        if (loseResponse) return route.abort();
        json = job;
      } else json = jobs;
    }
    await route.fulfill({ json });
  });
  await page.goto("/#nodes");
  await page.getByRole("button", { name: "SSH 安装节点", exact: true }).click();
  return { jobs, probes, submissions };
}

test("pin host key before password, clear secrets and restore background jobs", async ({
  page,
}) => {
  const state = await setup(page);
  await expect(page.getByLabel("登录密码", { exact: true })).toHaveCount(0);
  await page.getByLabel("主机地址").fill("10.0.0.20");
  await page.getByRole("button", { name: "检查连接与主机密钥" }).click();
  await expect(page.getByText("SHA256:verified-key")).toBeVisible();
  expect(state.probes).toEqual([{ host: "10.0.0.20", port: 22, user: "root" }]);
  await page.getByLabel("登录密码", { exact: true }).fill("super-secret");
  await expect(
    page.getByRole("button", { name: "安装节点", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("已核对主机密钥指纹").check();
  await page.getByRole("button", { name: "安装节点", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("安装 Docker", { exact: true })).toBeVisible();
  expect(state.submissions).toHaveLength(1);
  expect(state.submissions[0].fingerprint).toBe("SHA256:verified-key");
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain("super-secret");
  await page.reload();
  await expect(page.getByText("安装 Docker", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "SSH 安装节点", exact: true }).click();
  await page.getByLabel("主机地址").fill("10.0.0.21");
  await page.getByRole("button", { name: "检查连接与主机密钥" }).click();
  await expect(page.getByLabel("登录密码", { exact: true })).toHaveValue("");
  await page.getByLabel("主机地址").fill("10.0.0.22");
  await expect(page.getByText("SHA256:verified-key")).toHaveCount(0);
});

test("lost submit response reconciles the same job without a second install", async ({
  page,
}) => {
  const state = await setup(page, true);
  await page.getByLabel("主机地址").fill("10.0.0.20");
  await page.getByRole("button", { name: "检查连接与主机密钥" }).click();
  await page.getByLabel("已核对主机密钥指纹").check();
  await page.getByLabel("登录密码", { exact: true }).fill("secret");
  await page.getByRole("button", { name: "安装节点", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.submissions).toHaveLength(1);
  state.jobs[0].status = "failed";
  state.jobs[0].error_code = "docker_old";
  await page.reload();
  await page.getByText("最近安装记录（1）").click();
  await expect(page.getByText(/已有 Docker Engine 低于 20.10/)).toBeVisible();
});

test("SSH form stays usable on narrow screens", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.getByLabel("主机地址").fill("192.168.100.200");
  await page.getByLabel("登录账号", { exact: true }).fill("deploy");
  await page.getByRole("button", { name: "检查连接与主机密钥" }).click();
  await expect(page.getByLabel("sudo 密码（留空使用登录密码）")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "/private/tmp/ctl-ssh-install-mobile.png",
    fullPage: true,
  });
});
