import { test, expect } from "@playwright/test";

test("actual API project, secret, conflict, credential and logout", async ({
  page,
}) => {
  const token = process.env.CTL_OWNER_TOKEN;
  if (!token) throw new Error("temporary owner fixture required");
  await page.goto("/");
  await page.getByLabel("管理员凭据").fill(token);
  await page.getByRole("button", { name: "进入管理台" }).click();
  await page.getByRole("button", { name: "注册项目", exact: true }).click();
  await page.getByLabel("项目标识").fill("browser-notes");
  await page.getByLabel("项目名称").fill("Notes 浏览器验收");
  await page.getByLabel("项目说明").fill("管理 Notes 的发布版本与生产配置。");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "注册项目" })
    .click();
  await page.getByRole("button", { name: /Notes 浏览器验收/ }).click();
  await page.getByRole("tab", { name: "环境配置" }).click();
  await page.getByRole("button", { name: "添加变量" }).first().click();
  await page.getByLabel("业务变量变量名 1").fill("DATABASE_URL");
  await page.getByLabel("业务变量值 1").fill("private-fixture-only");
  await page.getByLabel("秘密", { exact: true }).first().check();
  await page.getByRole("button", { name: "审阅并保存" }).click();
  await page.getByRole("button", { name: "保存新修订" }).click();
  await expect(page.getByText("已保存修订 2。")).toBeVisible();
  await expect(page.getByLabel("业务变量值 1")).toBeDisabled();
  const origin = new URL(page.url()).origin;
  const current = await page.request.get(
    "/api/v1/projects/browser-notes/environments/prod",
  );
  expect(JSON.stringify(await current.json())).not.toContain(
    "private-fixture-only",
  );
  await page.request.put("/api/v1/projects/browser-notes/environments/prod", {
    headers: { Origin: origin },
    data: {
      expected_revision: 2,
      runtime_env: [
        { key: "REMOTE_CHANGED", operation: "set", value: "remote" },
      ],
    },
  });
  await page.getByRole("button", { name: "替换值" }).click();
  await page.getByLabel("业务变量值 1").fill("unsaved-draft");
  await page.getByRole("button", { name: "审阅并保存" }).click();
  await page.getByRole("button", { name: "保存新修订" }).click();
  await expect(
    page.getByText("服务器已有修订 3，当前草稿已保留。"),
  ).toBeVisible();
  await expect(page.getByLabel("业务变量值 1")).toHaveValue("unsaved-draft");
  await page.screenshot({
    path: "test-results/environment-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/environment-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.getByRole("tab", { name: "访问凭据" }).click();
  await page.getByRole("button", { name: "创建凭据", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("prod-host");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建凭据", exact: true })
    .click();
  await expect(page.getByTestId("new-token")).toContainText("ctl_");
  const minted = await page.getByTestId("new-token").textContent();
  const tokens = await page.request.get("/api/v1/tokens");
  expect(JSON.stringify(await tokens.json())).not.toContain(minted!);
  await page.getByRole("button", { name: "已保存，关闭展示" }).click();
  await expect(page.getByTestId("new-token")).toHaveCount(0);
  await page.getByRole("button", { name: "退出" }).click();
  await expect(page.getByLabel("管理员凭据")).toBeVisible();
  expect((await page.request.get("/api/v1/me")).status()).toBe(401);
});
