import { test, expect } from "@playwright/test";

test("group-first navigation, membership and in-context group authorization", async ({
  page,
}) => {
  const owner = process.env.CTL_OWNER_TOKEN;
  if (!owner) throw new Error("disposable owner fixture required");
  const suffix = Date.now().toString(36);
  const group = `workspace-${suffix}`,
    project = `app-${suffix}`,
    future = `future-${suffix}`;
  await page.goto("/");
  await page.getByLabel("管理员凭据").fill(owner);
  await page.getByRole("button", { name: "进入管理台" }).click();
  await expect(
    page.getByRole("heading", { name: "项目组", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "创建项目组", exact: true }).click();
  await page.getByLabel("项目组标识", { exact: true }).fill(group);
  await page.getByLabel("项目组名称", { exact: true }).fill("业务服务");
  await page.getByRole("button", { name: "保存项目组" }).click();
  await page
    .getByRole("link", { name: `查看项目组 ${group}`, exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "组内还没有项目" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "注册项目", exact: true }).click();
  await expect(page.getByLabel("所属项目组")).toHaveValue(group);
  await page.getByLabel("项目标识").fill(project);
  await page.getByLabel("项目名称").fill("订单服务");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "注册项目", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "打开项目 订单服务", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: /组授权/ }).click();
  await page.getByRole("button", { name: "创建组凭据", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByText(`授权项目组：${group}`, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("dialog").getByText("订单服务", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("名称", { exact: true }).fill(`group-host-${suffix}`);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建组凭据", exact: true })
    .click();
  const token = (await page.getByTestId("new-token").innerText()).trim();
  const headers = { Authorization: `Bearer ${token}` };
  expect(
    (
      await page.request.get(`/api/v1/projects/${project}`, { headers })
    ).status(),
  ).toBe(200);
  expect(
    (
      await page.request.post(`/api/v1/projects/${project}/resolve`, {
        headers,
        data: { environment: "test" },
      })
    ).status(),
  ).toBe(403);
  await page.getByRole("button", { name: "已保存，关闭展示" }).click();
  await page.screenshot({
    path: "test-results/group-authorization-desktop.png",
    fullPage: true,
  });
  const origin = new URL(page.url()).origin;
  // A future member starts outside the scope; adding it through the UI expands the same token.
  expect(
    (
      await page.request.post("/api/v1/projects", {
        headers: { Origin: origin },
        data: {
          slug: future,
          name: "库存服务",
          group: "default",
          default_environment: "prod",
        },
      })
    ).status(),
  ).toBe(201);
  expect(
    (
      await page.request.get(`/api/v1/projects/${future}`, { headers })
    ).status(),
  ).toBe(403);
  await page.getByRole("tab", { name: /组内项目/ }).click();
  await page.getByRole("button", { name: "加入已有项目", exact: true }).click();
  await page.getByLabel(`加入项目 ${future}`).check();
  await page.getByRole("button", { name: "确认加入", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "打开项目 库存服务", exact: true }),
  ).toBeVisible();
  expect(
    (
      await page.request.get(`/api/v1/projects/${future}`, { headers })
    ).status(),
  ).toBe(200);
  await page.screenshot({
    path: "test-results/group-members-desktop.png",
    fullPage: true,
  });
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .getByRole("button", { name: `移出项目 ${future}`, exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "打开项目 库存服务", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await page.request.get(`/api/v1/projects/${future}`, { headers })
    ).status(),
  ).toBe(403);
  await page
    .getByRole("link", { name: "打开项目 订单服务", exact: true })
    .click();
  await page
    .getByRole("button", { name: "返回所属项目组", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "业务服务", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("link", { name: "打开项目 订单服务", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/group-projects-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/group-projects-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.getByRole("tab", { name: /组授权/ }).click();
  await expect(
    page.getByRole("row").filter({ hasText: `group-host-${suffix}` }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/group-authorization-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .getByRole("row")
    .filter({ hasText: `group-host-${suffix}` })
    .getByRole("button", { name: "撤销", exact: true })
    .click();
  await expect(
    page.getByRole("row").filter({ hasText: `group-host-${suffix}` }),
  ).toHaveCount(0);
  expect(
    (
      await page.request.get(`/api/v1/projects/${project}`, { headers })
    ).status(),
  ).toBe(401);
});
