import { test, expect, type Page } from "@playwright/test";

async function fixture(page: Page) {
  const project = {
    slug: "notes",
    name: "Notes",
    group: "apps",
    default_environment: "prod",
  };
  const base = {
    enabled: true,
    groups: ["apps"],
    projects: [],
    excluded_projects: [],
    environments: ["prod"],
    created_at: "2026-11-05T10:00:00Z",
    last_seen: null,
    agent_version: "1",
    platform: "linux",
    docker_available: true,
    online: false,
  };
  const nodes = [
    { ...base, id: "node-a", name: "Server A" },
    { ...base, id: "node-b", name: "Server B" },
  ];
  const tasks: Record<string, unknown>[] = [];
  const bodies: Record<string, unknown>[] = [];
  let loseResponse = true;
  const gets: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    if (request.method() === "GET") gets.push(path);
    let json: unknown = [];
    if (path === "/me") json = { role: "owner" };
    if (path === "/groups") json = [{ slug: "apps", name: "Apps" }];
    if (path === "/projects") json = [project];
    if (path === "/projects/notes") json = project;
    if (path.endsWith("/environments")) json = ["prod"];
    if (path.endsWith("/releases"))
      json = [{ id: "release", version: "v2", status: "published" }];
    if (path === "/nodes" && request.method() === "POST") {
      const body = request.postDataJSON();
      const node = { ...base, ...body, id: "node-c" };
      nodes.push(node);
      return route.fulfill({
        status: 201,
        json: {
          node,
          enrollment_token: "single-use-secret",
          expires_at: "2026-11-05T12:00:00Z",
        },
      });
    }
    if (path === "/nodes" || path === "/projects/notes/nodes") json = nodes;
    if (path === "/nodes/node-a" && request.method() === "PATCH") {
      Object.assign(nodes[0], request.postDataJSON());
      json = nodes[0];
    }
    if (path === "/nodes/node-a/enrollment")
      json = {
        enrollment_token: "rotated-enrollment",
        expires_at: "2026-11-05T12:00:00Z",
      };
    if (path === "/projects/notes/instances")
      json = [
        {
          id: "instance-a",
          node_id: "node-a",
          project: "notes",
          environment: "prod",
          actual_version: "v1",
          health: "healthy",
          observed_at: "2020-01-01T00:00:00Z",
          binding: { host_port: 8080, bind_address: "127.0.0.1" },
          pending_transaction: false,
        },
        {
          id: "instance-b",
          node_id: "node-b",
          project: "notes",
          environment: "prod",
          actual_version: "v2",
          health: "unknown",
          observed_at: null,
          binding: null,
          pending_transaction: true,
        },
      ];
    if (path === "/projects/notes/tasks" && request.method() === "POST") {
      const body = request.postDataJSON();
      bodies.push(body);
      if (!tasks.length)
        tasks.push({
          ...body,
          id: "task-a",
          instance_id: "instance-a",
          project: "notes",
          actor: "owner",
          status: "queued",
          stage: "accepted",
          created_at: "2026-11-05T10:00:00Z",
          updated_at: "2026-11-05T10:00:00Z",
          result: {},
        });
      if (loseResponse) {
        loseResponse = false;
        return route.abort("failed");
      }
      return route.fulfill({ status: 201, json: tasks[0] });
    }
    if (path === "/projects/notes/tasks/task-a/cancel") {
      tasks[0].status = "cancelled";
      json = tasks[0];
    }
    if (path === "/projects/notes/tasks") json = tasks;
    await route.fulfill({ json });
  });
  return { bodies, gets, tasks };
}

test("node enrollment is one-time and deployment retry preserves identity across two-node state", async ({
  page,
}) => {
  const { bodies } = await fixture(page);
  await page.goto("/#nodes");
  await page.getByRole("button", { name: "创建节点", exact: true }).click();
  await page.getByLabel("节点名称").fill("Server C");
  await page.getByLabel("授权项目组 apps").check();
  await page.getByLabel("允许环境").fill("prod");
  await page.getByRole("button", { name: "保存节点", exact: true }).click();
  await expect(page.getByLabel("一次性注册凭据")).toHaveValue(
    "single-use-secret",
  );
  expect(
    await page.evaluate(() =>
      JSON.stringify({
        local: { ...localStorage },
        session: { ...sessionStorage },
        href: location.href,
      }),
    ),
  ).not.toContain("single-use-secret");
  await page.getByRole("button", { name: "已保存，关闭凭据" }).click();
  await expect(page.getByText("single-use-secret")).toHaveCount(0);
  await page.goto("/#project/notes");
  await page.getByRole("tab", { name: "部署", exact: true }).click();
  await expect(
    page.getByRole("table", { name: "已部署实例" }).getByRole("row"),
  ).toHaveCount(3);
  await expect(page.getByRole("table", { name: "已部署实例" })).toContainText(
    "上次观测 · 健康",
  );
  await expect(
    page
      .getByRole("table", { name: "已部署实例" })
      .getByRole("row")
      .filter({ hasText: "Server B" }),
  ).toContainText("v2");
  await page.getByLabel("目标节点").selectOption("node-a");
  await page.getByLabel("操作", { exact: true }).selectOption("upgrade");
  await page.getByLabel("部署版本").selectOption("v2");
  await page.getByLabel("主机端口").fill("8081");
  await page.getByRole("button", { name: "创建部署任务", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("结果尚未确认");
  await expect(page.getByLabel("目标节点")).toBeDisabled();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("link", { name: "部署节点", exact: true }).click();
  await expect(page).toHaveURL(/#project\/notes$/);
  await page
    .getByRole("button", { name: "重试原任务请求", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "任务已创建" }),
  ).toBeVisible();
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0].request_id).toMatch(/^[a-f0-9]{32}$/);
  await page.getByRole("button", { name: "取消任务 task-a" }).click();
  await expect(page.getByRole("table", { name: "部署任务" })).toContainText(
    "已取消",
  );
  await page.getByLabel("目标节点").selectOption("node-b");
  await page.getByLabel("操作", { exact: true }).selectOption("upgrade");
  await expect(
    page.getByRole("button", { name: "创建部署任务" }),
  ).toBeDisabled();
  await page.getByLabel("操作", { exact: true }).selectOption("rollback");
  await expect(page.getByLabel("部署版本")).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "创建部署任务" }),
  ).toBeEnabled();
});

test("node authorization validates environments, edit replaces grants and reenrollment token disappears on navigation", async ({
  page,
}) => {
  await fixture(page);
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/#nodes");
  await page.getByRole("button", { name: "编辑节点 Server A" }).click();
  await page.getByLabel("允许环境").fill("");
  await page.getByRole("button", { name: "保存节点" }).click();
  await expect(page.getByRole("alert")).toContainText("至少一个环境");
  await page.getByLabel("允许环境").fill("staging");
  await page.getByLabel("排除项目 notes").check();
  await page.getByRole("button", { name: "保存节点" }).click();
  const row = page.getByRole("row").filter({ hasText: "Server A" });
  await expect(row).toContainText("环境 staging");
  await expect(row).toContainText("排除 notes");
  await page.getByRole("button", { name: "禁用节点 Server A" }).click();
  await expect(row).toContainText("已禁用");
  await page.getByRole("button", { name: "启用节点 Server A" }).click();
  await expect(row).toContainText("失联");
  await page.getByRole("button", { name: "重新登记节点 Server A" }).click();
  await expect(page.getByLabel("一次性注册凭据")).toHaveValue(
    "rotated-enrollment",
  );
  await page.goto("/#projects");
  await page.getByRole("link", { name: "部署节点", exact: true }).click();
  await expect(page.getByLabel("一次性注册凭据")).toHaveCount(0);
});

test("deployment polling stops on tab departure and mobile layout stays within viewport", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const { gets } = await fixture(page);
  await page.goto("/#project/notes");
  await page.getByRole("tab", { name: "部署", exact: true }).click();
  await expect(page.getByRole("table", { name: "已部署实例" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("mobile.png"),
    fullPage: true,
  });
  const before = gets.filter((path) => path === "/projects/notes/tasks").length;
  await expect
    .poll(
      () => gets.filter((path) => path === "/projects/notes/tasks").length,
      { timeout: 10000 },
    )
    .toBeGreaterThan(before);
  await page.getByRole("tab", { name: "版本", exact: true }).click();
  const stopped = gets.filter(
    (path) => path === "/projects/notes/tasks",
  ).length;
  await page.waitForTimeout(5500);
  expect(gets.filter((path) => path === "/projects/notes/tasks").length).toBe(
    stopped,
  );
});

test("tasks follow the selected environment and missing node metadata keeps connection unknown", async ({
  page,
}) => {
  const { tasks } = await fixture(page);
  const running = {
    instance_id: "instance",
    project: "notes",
    actor: "owner",
    status: "running",
    stage: "deploying",
    action: "upgrade",
    version: "v2",
    created_at: "2026-11-05T10:00:00Z",
    updated_at: "2026-11-05T10:00:00Z",
    result: null,
  };
  tasks.push(
    {
      ...running,
      id: "staging-task",
      node_id: "staging-only-node",
      environment: "staging",
    },
    {
      ...running,
      id: "prod-task",
      node_id: "disabled-node",
      environment: "prod",
    },
  );
  await page.goto("/#project/notes");
  await page.getByRole("tab", { name: "部署", exact: true }).click();
  const table = page.getByRole("table", { name: "部署任务" });
  await expect(table).not.toContainText("staging-task");
  await expect(table).toContainText("prod-task");
  await expect(table).toContainText("执行中 · 连接状态未知");
});
