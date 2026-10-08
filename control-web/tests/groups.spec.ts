import { test, expect } from "@playwright/test";

test("group assignment and one login authorizes multiple projects", async ({
  page,
}) => {
  const owner = process.env.CTL_OWNER_TOKEN;
  if (!owner) throw new Error("disposable owner fixture required");
  const suffix = Date.now().toString(36),
    group = `browser-apps-${suffix}`,
    notes = `group-notes-${suffix}`,
    config = `group-config-${suffix}`;
  await page.goto("/");
  await page.getByLabel("管理员凭据").fill(owner);
  await page.getByRole("button", { name: "进入管理台" }).click();
  await page.getByRole("link", { name: "项目组", exact: true }).click();
  await expect(
    page.getByText("default", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "创建项目组", exact: true }).click();
  await page.getByLabel("项目组标识", { exact: true }).fill(group);
  await page
    .getByLabel("项目组名称", { exact: true })
    .fill(`应用服务 ${suffix}`);
  await page.getByRole("button", { name: "保存项目组" }).click();
  await expect(
    page.getByRole("heading", { name: `应用服务 ${suffix}` }),
  ).toBeVisible();
  for (const slug of [notes, config]) {
    await page.getByRole("link", { name: "全部项目", exact: true }).click();
    await page.getByRole("button", { name: "注册项目", exact: true }).click();
    await page.getByLabel("项目标识").fill(slug);
    await page.getByLabel("项目名称").fill(slug);
    await page.getByLabel("所属项目组").selectOption(group);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "注册项目", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  }
  await page.getByRole("link", { name: "组凭据", exact: true }).click();
  await page.getByRole("button", { name: "创建凭据", exact: true }).click();
  await page
    .getByLabel("名称", { exact: true })
    .fill(`shared-prod-host-${suffix}`);
  await page.getByLabel(`授权项目组 ${group}`).check();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建凭据", exact: true })
    .click();
  const token = (await page.getByTestId("new-token").innerText()).trim();
  expect(token.startsWith("ctl_")).toBe(true);
  const headers = { Authorization: `Bearer ${token}` };
  const listing = await page.request.get("/api/v1/projects", { headers });
  expect(listing.status()).toBe(200);
  expect(
    (await listing.json()).map((p: { slug: string }) => p.slug).sort(),
  ).toEqual([config, notes].sort());
  for (const slug of [notes, config]) {
    const res = await page.request.get(`/api/v1/projects/${slug}`, { headers });
    expect(res.status()).toBe(200);
  }
  const forbidden = await page.request.post(
    `/api/v1/projects/${notes}/resolve`,
    { headers, data: { environment: "test" } },
  );
  expect(forbidden.status()).toBe(403);
  await page.getByRole("button", { name: "已保存，关闭展示" }).click();
  expect(
    await page.evaluate(
      () =>
        JSON.stringify({ ...localStorage }) +
        JSON.stringify({ ...sessionStorage }),
    ),
  ).not.toContain(token);
  // An unrelated save from a stale tab must not undo a membership move by another owner tab.
  await page.getByRole("link", { name: "全部项目", exact: true }).click();
  await page
    .getByRole("link", { name: `打开项目 ${notes}`, exact: true })
    .click();
  await page.getByRole("button", { name: "项目设置", exact: true }).click();
  const prior = await page.request.get(`/api/v1/projects/${notes}`);
  const moved = await page.request.patch(`/api/v1/projects/${notes}`, {
    headers: { Origin: new URL(page.url()).origin },
    data: { ...(await prior.json()), group: "default" },
  });
  expect(moved.status()).toBe(200);
  await page.getByLabel("项目说明").fill("Updated from a stale settings tab");
  await page.getByRole("button", { name: "保存项目", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const denied = await page.request.get(`/api/v1/projects/${notes}`, {
    headers,
  });
  expect(denied.status()).toBe(403);
  await page.getByRole("link", { name: "项目组", exact: true }).click();
  await page.screenshot({
    path: "test-results/groups-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/groups-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});
