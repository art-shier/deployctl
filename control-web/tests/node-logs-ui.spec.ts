import { test, expect, type Page } from "@playwright/test";
async function fixture(page: Page, multiService = false) {
  const queries: any[] = [];
  const project = {
    slug: "notes",
    name: "Notes",
    group: "apps",
    default_environment: "prod",
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let json: any = [];
    if (path.endsWith("/me")) json = { role: "owner" };
    if (path.endsWith("/groups")) json = [{ slug: "apps", name: "Apps" }];
    if (path === "/api/v1/projects") json = [project];
    if (path === "/api/v1/projects/notes") json = project;
    if (path.endsWith("/environments")) json = ["prod"];
    if (path.endsWith("/releases") && multiService)
      json = [
        {
          id: "r",
          version: "v3",
          status: "published",
          images: {
            api: "registry.example/api@sha256:" + "c".repeat(64),
            postgres: "registry.example/db@sha256:" + "b".repeat(64),
          },
          minimum_deployctl_version: "1.20.0",
        },
      ];
    if (path.endsWith("/nodes"))
      json = [
        {
          id: "node-a",
          name: "Server A",
          enabled: true,
          online: true,
          environments: ["prod"],
          projects: ["notes"],
          groups: [],
        },
      ];
    if (path.endsWith("/instances"))
      json = [
        {
          id: "instance-a",
          node_id: "node-a",
          project: "notes",
          environment: "prod",
          actual_version: "v2",
          ...(multiService
            ? {
                stack: {
                  services: {
                    api: {
                      version: "v2",
                      image: "registry.example/api@sha256:" + "a".repeat(64),
                      lifecycle: "application",
                      health: "healthy",
                    },
                    postgres: {
                      version: "v1",
                      image: "registry.example/db@sha256:" + "b".repeat(64),
                      lifecycle: "persistent",
                      health: "healthy",
                    },
                  },
                  jobs: { restore: "succeeded" },
                  operation_id: "",
                  phase: "",
                  recovery_required: false,
                },
              }
            : {}),
          health: "healthy",
          observed_at: new Date().toISOString(),
        },
      ];
    if (path.endsWith("/tasks"))
      json = [
        {
          id: "task-a",
          instance_id: "instance-a",
          node_id: "node-a",
          project: "notes",
          environment: "prod",
          action: "upgrade",
          status: "failed",
          stage: "execution_failed",
          version: "v2",
        },
      ];
    if (path.endsWith("/logs")) {
      const body = route.request().postDataJSON();
      queries.push(body);
      if (body.search === "offline")
        return route.fulfill({
          status: 503,
          json: { message: "节点离线，无法拉取日志" },
        });
      json = {
        text:
          body.source === "deployment"
            ? "Starting container failed\n"
            : "2026-11-09T10:00:00Z hello <script>danger</script>\n",
        truncated: true,
        sources: [],
        error: "",
      };
      if (body.source === "archives")
        json = {
          text: "",
          truncated: false,
          error: "",
          sources: [
            {
              id: "archive-a",
              version: "v1",
              captured_at: "2026-11-09T09:00:00Z",
            },
          ],
        };
      if (body.source === "archive") json.text = "old version output";
    }
    await route.fulfill({ json });
  });
  await page.goto("/#project/notes");
  await page.getByRole("tab", { name: "部署", exact: true }).click();
  return queries;
}
test("runtime logs filter, archive, download and close without losing deployment context", async ({
  page,
}) => {
  const queries = await fixture(page);
  await page.getByRole("button", { name: "运行日志", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("日志内容")).toContainText(
    "<script>danger</script>",
  );
  await expect(dialog).toContainText("结果已截断");
  await dialog.screenshot({ path: test.info().outputPath("logs-desktop.png") });
  await dialog.getByLabel("关键词").fill("offline");
  await dialog.getByRole("button", { name: "查询", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("节点离线");
  await dialog.getByLabel("关键词").fill("");
  await dialog.getByRole("button", { name: "查询", exact: true }).click();
  await dialog.getByLabel("日志来源").selectOption("archives");
  await dialog.getByLabel("历史归档").selectOption("archive-a");
  await expect(dialog.getByLabel("日志内容")).toContainText(
    "old version output",
  );
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载当前结果" }).click();
  expect((await download).suggestedFilename()).toContain("notes");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(queries.some((q) => q.source === "archive")).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "运行日志", exact: true }),
  ).toBeFocused();
});
test("task entry requests the selected task and narrow layout stays inside viewport", async ({
  page,
}) => {
  const queries = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "部署日志", exact: true }).click();
  await expect(page.getByLabel("日志内容")).toContainText(
    "Starting container failed",
  );
  expect(queries.at(-1).task_id).toBe("task-a");
  await expect(page.getByRole("dialog")).toContainText("prod");
  const box = await page.getByRole("dialog").boundingBox();
  expect(box!.width).toBeLessThanOrEqual(390);
  await page
    .getByRole("dialog")
    .screenshot({ path: test.info().outputPath("logs-mobile.png") });
});

test("switching source cancels a slow response and does not show stale logs", async ({
  page,
}) => {
  await fixture(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/instances/*/logs", async (route) => {
    const runtime = route.request().postDataJSON().source === "runtime";
    if (runtime) await held;
    await route
      .fulfill({
        json: {
          text: runtime ? "stale runtime" : "fresh failure",
          truncated: false,
          sources: [],
          error: "",
        },
      })
      .catch(() => {});
  });
  await page.getByRole("button", { name: "运行日志", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText(
    "正在从节点",
  );
  await page.getByLabel("日志来源").selectOption("failure");
  await expect(page.getByLabel("日志内容")).toContainText("fresh failure");
  release();
  await expect(page.getByLabel("日志内容")).not.toContainText("stale runtime");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("multi-service state and selected database logs stay separate", async ({
  page,
}) => {
  const queries = await fixture(page, true);
  await page.getByLabel("部署版本").selectOption("v3");
  await page.getByText("查看版本镜像（2 个组件）", { exact: true }).click();
  await expect(page.getByText(/节点 ctl 至少 1.20.0/)).toBeVisible();
  await page.getByText("服务状态", { exact: true }).click();
  await expect(
    page.getByText("postgres", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("持久服务", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "运行日志", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("服务或任务").selectOption("postgres");
  await expect.poll(() => queries.at(-1)?.service).toBe("postgres");
  await dialog.getByLabel("日志来源").selectOption("failure");
  await expect.poll(() => queries.at(-1)?.source).toBe("failure");
  expect(queries.at(-1)).not.toHaveProperty("service");
  await dialog.getByLabel("日志来源").selectOption("runtime");
  await page.setViewportSize({width:390,height:844});
  await expect(dialog.getByLabel("服务或任务")).toHaveValue("postgres");
  await dialog.screenshot({path:test.info().outputPath("multi-service-logs-mobile.png")});
  await page.keyboard.press("Escape");
  await page.screenshot({path:test.info().outputPath("multi-service-details-mobile.png"),fullPage:true});
});
