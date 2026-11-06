import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/me"))
      return route.fulfill({ status: 401, json: { message: "未登录" } });
    if (path.endsWith("/session"))
      return route.fulfill({ status: 401, json: { message: "凭据无效" } });
    return route.fulfill({ json: [] });
  });
});

test("首页介绍和弹窗关闭后清理凭据", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "远程节点部署" }),
  ).toBeVisible();
  await expect(page.getByLabel("管理员凭据")).toHaveCount(0);
  const login = page.getByRole("button", { name: "登录", exact: true });
  await login.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("管理员凭据").fill("test-only");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(login).toBeFocused();
  await page.getByRole("button", { name: "登录管理台", exact: true }).click();
  await expect(page.getByLabel("管理员凭据")).toHaveValue("");
  await page.getByLabel("管理员凭据").fill("invalid");
  await page.getByRole("button", { name: "进入管理台" }).click();
  await expect(page.getByRole("alert")).toHaveText("凭据无效");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await login.click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.locator(".overlay").click({ position: { x: 5, y: 5 } });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.screenshot({
    path: "test-results/landing-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.screenshot({
    path: "test-results/landing-mobile.png",
    fullPage: true,
  });
  await login.click();
  await page.screenshot({
    path: "test-results/landing-login.png",
    fullPage: true,
  });
});

test("登录成功进入管理台，退出返回首页", async ({ page }) => {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ status: 204 }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("管理员凭据").fill("fixture-only");
  await page.getByRole("button", { name: "进入管理台" }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await page.getByRole("button", { name: /退出/ }).click();
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
