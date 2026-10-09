import { expect, test } from "@playwright/test";

test("owner removes projects and empty groups, identifiers stay reserved and archived credential scopes can be edited", async ({
  page,
}) => {
  test.setTimeout(90000);
  const owner = process.env.CTL_OWNER_TOKEN;
  if (!owner) throw new Error("disposable owner fixture required");
  const suffix = Date.now().toString(36),
    group = `remove-${suffix}`,
    project = `remove-app-${suffix}`,
    name = `publisher-${suffix}`;
  await page.goto("/");
  await page.getByLabel("管理员凭据").fill(owner);
  await page.getByRole("button", { name: "进入管理台" }).click();
  await expect(
    page.getByRole("heading", { name: "项目组", exact: true }),
  ).toBeVisible();
  const headers = { Origin: new URL(page.url()).origin };
  const groupBody = { slug: group, name: "待移除项目组" };
  const projectBody = {
    slug: project,
    name: "待移除项目",
    group,
    default_environment: "prod",
  };
  expect(
    (
      await page.request.post("/api/v1/groups", { headers, data: groupBody })
    ).status(),
  ).toBe(201);
  expect(
    (
      await page.request.post("/api/v1/projects", {
        headers,
        data: projectBody,
      })
    ).status(),
  ).toBe(201);
  expect(
    (
      await page.request.post("/api/v1/tokens", {
        headers,
        data: {
          name,
          role: "publisher",
          groups: [group],
          projects: [project],
          environments: [],
          expires_at: new Date(Date.now() + 86400000).toISOString(),
        },
      })
    ).status(),
  ).toBe(201);
  await page.goto(`/#group/${group}/settings`);
  await expect(
    page.getByRole("button", { name: "移除项目组", exact: true }),
  ).toBeDisabled();
  await page.goto(`/#project/${project}`);
  await page.getByRole("button", { name: "项目设置", exact: true }).click();
  await page.getByRole("button", { name: "移除项目", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认移除项目", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("输入项目标识确认").fill(project);
  await page.screenshot({
    path: "test-results/project-remove-real-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "确认移除项目", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`#group/${group}$`));
  await expect(
    page.getByRole("heading", { name: "组内还没有项目", exact: true }),
  ).toBeVisible();
  expect((await page.request.get(`/api/v1/projects/${project}`)).status()).toBe(
    404,
  );
  expect(
    (
      await page.request.post("/api/v1/projects", {
        headers,
        data: projectBody,
      })
    ).status(),
  ).toBe(409);
  await page.getByRole("tab", { name: "组设置", exact: true }).click();
  await page.getByRole("button", { name: "移除项目组", exact: true }).click();
  await page.getByLabel("输入项目组标识确认").fill(group);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/group-remove-real-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page
    .getByRole("button", { name: "确认移除项目组", exact: true })
    .click();
  await expect(page).toHaveURL(/#groups$/);
  await expect(
    page.getByRole("link", { name: `查看项目组 ${group}`, exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await page.request.post("/api/v1/groups", { headers, data: groupBody })
    ).status(),
  ).toBe(409);
  expect(
    (await page.request.delete("/api/v1/groups/default", { headers })).status(),
  ).toBe(409);
  await page.goto("/#tokens");
  const row = page.getByRole("row").filter({ hasText: name });
  await row.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel(`授权项目组 ${group}`)).toBeChecked();
  await expect(page.getByLabel(`单独授权项目 ${project}`)).toBeChecked();
  await expect(page.getByRole("dialog")).toContainText("已移除");
  await page.getByLabel(`授权项目组 ${group}`).click();
  await page.getByLabel(`单独授权项目 ${project}`).click();
  await page.getByLabel("授权项目组 default").check();
  await page.getByRole("button", { name: "保存凭据", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const credentials = await (await page.request.get("/api/v1/tokens")).json();
  const saved = credentials.find(
    (credential: { name: string }) => credential.name === name,
  );
  expect(saved).toMatchObject({ groups: ["default"], environments: [] });
  expect(saved.projects || []).toEqual([]);
});
