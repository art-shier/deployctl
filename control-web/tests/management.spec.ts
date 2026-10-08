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
  const imageArchive = process.env.CTL_BROWSER_IMAGE_ARCHIVE;
  if (!imageArchive) throw new Error("real Docker archive fixture required");
  await page.getByRole("tab", { name: "镜像管理" }).click();
  await expect(page.getByTestId("image-count")).toHaveText("0");
  for (const tag of ["manual", "manual-alias"]) {
    await page.getByRole("button", { name: "上传镜像", exact: true }).click();
    await page.getByLabel("目标标签").fill(tag);
    await page.getByLabel("镜像 tar 文件").setInputFiles(imageArchive);
    await page.getByRole("button", { name: "上传并入库" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByTestId("image-count")).toHaveText("1");
  }
  await expect(page.getByTestId("image-tag-count")).toHaveText("2");
  await page.getByRole("button", { name: "详情", exact: true }).click();
  await expect(page.getByRole("dialog").getByText("linux/amd64")).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.screenshot({
    path: "test-results/images-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/images-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.getByLabel("搜索镜像").fill("no-such-image");
  await expect(page.getByText("没有匹配的镜像")).toBeVisible();
  await page.getByLabel("搜索镜像").fill("manual");
  await page.getByRole("button", { name: /删除镜像 manual/ }).click();
  await expect(
    page.getByRole("dialog").getByText("manual-alias", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "确认删除镜像" }).click();
  await expect(page.getByTestId("image-count")).toHaveText("0");
  await expect(page.getByTestId("image-tag-count")).toHaveText("0");
  await page.getByRole("button", { name: "上传镜像", exact: true }).click();
  await page.getByLabel("目标标签").fill("bad-archive");
  await page
    .getByLabel("镜像 tar 文件")
    .setInputFiles({
      name: "invalid.tar",
      mimeType: "application/x-tar",
      buffer: Buffer.from("invalid archive"),
    });
  await page.getByRole("button", { name: "上传并入库" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "镜像文件无效",
  );
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 1000 });
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
