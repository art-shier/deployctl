import { test, expect } from "@playwright/test";

test("real group configuration inheritance, credential edits, reveal and revoke", async ({
  page,
}) => {
  test.setTimeout(90000);
  const owner = process.env.CTL_OWNER_TOKEN;
  if (!owner) throw new Error("disposable owner fixture required");
  const suffix = Date.now().toString(36),
    group = `config-${suffix}`,
    project = `service-${suffix}`,
    future = `future-${suffix}`;
  await page.goto("/");
  await page.getByLabel("管理员凭据").fill(owner);
  await page.getByRole("button", { name: "进入管理台" }).click();
  await expect(
    page.getByRole("heading", { name: "项目组", exact: true }),
  ).toBeVisible();
  const headers = { Origin: new URL(page.url()).origin };
  expect(
    (
      await page.request.post("/api/v1/groups", {
        headers,
        data: { slug: group, name: "共享配置验收" },
      })
    ).status(),
  ).toBe(201);
  expect(
    (
      await page.request.post("/api/v1/projects", {
        headers,
        data: {
          slug: project,
          name: "继承配置服务",
          group,
          default_environment: "prod",
        },
      })
    ).status(),
  ).toBe(201);
  await page.goto(`/#group/${group}/environments`);
  await expect(
    page.getByRole("heading", { name: "尚未配置环境" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "新增环境" }).click();
  await page.getByLabel("环境名称").fill("prod");
  await page.getByRole("button", { name: "创建环境", exact: true }).click();
  const business = page.locator("section.panel").filter({
    has: page.getByRole("heading", { name: "业务变量", exact: true }),
  });
  await business.getByRole("button", { name: "添加变量" }).click();
  await page.getByLabel("业务变量变量名 1").fill("TEXT");
  await page.getByLabel("业务变量值 1").fill("group-default");
  await business.getByRole("button", { name: "添加变量" }).click();
  await page.getByLabel("业务变量变量名 2").fill("DB_PASSWORD");
  await page.getByLabel("业务变量值 2").fill("group-secret-fixture");
  await business.getByLabel("秘密", { exact: true }).nth(1).check();
  const save = async () => {
    await page.getByRole("button", { name: "审阅并保存" }).click();
    await expect(page.getByRole("dialog")).not.toContainText(
      "group-secret-fixture",
    );
    await page.getByRole("button", { name: "保存新修订" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("已保存修订");
  };
  await save();
  const groupConfig = await page.request.get(
    `/api/v1/groups/${group}/environments/prod`,
  );
  const groupValue = await groupConfig.json();
  expect(
    groupValue.runtime_env.find(
      (row: { key: string }) => row.key === "DB_PASSWORD",
    ),
  ).not.toHaveProperty("value");
  await expect(business.locator('input[type="password"]')).toHaveValue("");
  const textValue = business
    .locator(".variable-row")
    .filter({ has: page.locator('input[value="TEXT"]') })
    .locator('input:not([type="checkbox"])')
    .nth(1);
  await textValue.fill("conflicting-draft");
  expect(
    (
      await page.request.put(`/api/v1/groups/${group}/environments/prod`, {
        headers,
        data: {
          expected_revision: groupValue.revision,
          runtime_env: [
            { key: "DB_PASSWORD", operation: "keep", secret: true },
            {
              key: "TEXT",
              operation: "set",
              secret: false,
              value: "group-default",
            },
          ],
          install_params: [],
        },
      })
    ).status(),
  ).toBe(200);
  await page.getByRole("button", { name: "审阅并保存" }).click();
  await page.getByRole("button", { name: "保存新修订" }).click();
  await expect(page.getByText(/当前草稿已保留/)).toBeVisible();
  await expect(textValue).toHaveValue("conflicting-draft");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "加载最新配置" }).click();
  await expect(textValue).toHaveValue("group-default");
  await page.screenshot({
    path: "test-results/group-environments-desktop.png",
    fullPage: true,
  });

  await page.goto(`/#project/${project}`);
  await page.getByRole("tab", { name: "环境配置", exact: true }).click();
  await expect(page.getByText("已配置 · 秘密值")).toBeVisible();
  await page.getByRole("button", { name: "覆盖 TEXT", exact: true }).click();
  await page.getByLabel("业务变量值 1").fill("project-override");
  await save();
  await expect(page.getByLabel("业务变量值 1")).toHaveValue("project-override");
  await page.getByRole("button", { name: "删除 TEXT", exact: true }).click();
  await expect(
    page.locator(".inherited-variable").filter({ hasText: "TEXT" }),
  ).toContainText("group-default");
  await save();
  const projectConfig = await (
    await page.request.get(`/api/v1/projects/${project}/environments/prod`)
  ).json();
  expect(projectConfig.runtime_env).toEqual([]);
  expect(projectConfig.inherited_runtime_env).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ key: "TEXT", value: "group-default" }),
    ]),
  );

  await page.goto(`/#group/${group}/access`);
  await page.getByRole("button", { name: "创建组凭据", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill(`editable-${suffix}`);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建组凭据", exact: true })
    .click();
  const token = (await page.getByTestId("new-token").innerText()).trim();
  const bearer = { Authorization: `Bearer ${token}` };
  const tokenList = await (await page.request.get("/api/v1/tokens")).json();
  const credential = tokenList.find(
    (item: { name: string }) => item.name === `editable-${suffix}`,
  );
  await page.getByRole("button", { name: "已保存，关闭展示" }).click();
  await page.getByRole("button", { name: "查看 Token", exact: true }).click();
  await expect(page.getByTestId("revealed-token")).toHaveText(token);
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByText(token, { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel(`允许项目 ${project}`).uncheck();
  await expect(page.getByText("已排除").first()).toBeVisible();
  await page.getByRole("button", { name: "保存凭据", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(
    (
      await page.request.get(`/api/v1/projects/${project}`, { headers: bearer })
    ).status(),
  ).toBe(403);
  expect(
    (
      await page.request.post("/api/v1/projects", {
        headers,
        data: {
          slug: future,
          name: "后续加入项目",
          group,
          default_environment: "prod",
        },
      })
    ).status(),
  ).toBe(201);
  expect(
    (
      await page.request.get(`/api/v1/projects/${future}`, { headers: bearer })
    ).status(),
  ).toBe(200);

  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel(`授权项目组 ${group}`).uncheck();
  await page.getByLabel(`单独授权项目 ${project}`).check();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/credential-edit-real-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.getByRole("button", { name: "保存凭据", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(
    (
      await page.request.get(`/api/v1/projects/${future}`, { headers: bearer })
    ).status(),
  ).toBe(403);
  expect(
    (
      await page.request.get(`/api/v1/projects/${project}`, { headers: bearer })
    ).status(),
  ).toBe(200);
  await page.goto("/#tokens");
  const row = page.getByRole("row").filter({ hasText: `editable-${suffix}` });
  await expect(row).toContainText(project);
  page.once("dialog", (dialog) => dialog.accept());
  await row.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(row).toHaveCount(0);
  expect(
    (await page.request.get(`/api/v1/tokens/${credential.id}/secret`)).status(),
  ).toBe(404);
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain(token);
});
