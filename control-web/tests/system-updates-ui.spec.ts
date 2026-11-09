import { test, expect, type Page } from "@playwright/test";
const releases = [
  {
    version: "v1.16.0",
    published_at: "2026-11-06T00:00:00Z",
    cli_sha256: "a".repeat(64),
    server_sha256: "b".repeat(64),
  },
];
const updater = (id: string, kind = "node") => ({
  id: "u-" + id,
  kind,
  target_id: id,
  current_version: "v1.15.0",
  helper_version: "v1.15.0",
  protocol: 1,
  platform: "linux",
  ready: true,
  blocked_reason: "",
  online: true,
  last_seen: "2026-11-06T00:00:00Z",
});

test("registered catalog accepts manual metadata and retains inputs on conflict", async ({
  page,
}) => {
  await fixture(page, { catalog: { source: "registered", stale: false } });
  let calls = 0;
  await page.route("**/api/v1/system-updates/releases", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.version).toBe("v1.17.0");
    expect(body.cli_url).toContain("/v1.17.0/deployctl.pyz");
    calls++;
    await route.fulfill(
      calls === 1
        ? { status: 409, json: { message: "conflict" } }
        : { status: 201, json: body },
    );
  });
  await page.goto("/#system-updates");
  await expect(page.getByRole("button", { name: "立即同步清单" })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "登记版本", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("版本号", { exact: true }).fill("v1.17.0");
  await dialog.getByRole("button", { name: "填入 GitHub 下载地址" }).click();
  await dialog.getByLabel("CLI SHA256", { exact: true }).fill("a".repeat(64));
  await dialog
    .getByLabel("中心安装包 SHA256", { exact: true })
    .fill("b".repeat(64));
  await dialog.getByRole("button", { name: "保存版本" }).click();
  await expect(dialog.getByRole("alert")).toContainText("不能覆盖已有版本");
  await expect(dialog.getByLabel("版本号", { exact: true })).toHaveValue(
    "v1.17.0",
  );
  await dialog.getByRole("button", { name: "保存版本" }).click();
  await expect(dialog).toHaveCount(0);
  expect(calls).toBe(2);
});

test("pipeline credential is masked and cleared when closed on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, { catalog: { source: "registered", stale: false } });
  let configured = false;
  await page.route(
    "**/api/v1/system-updates/registration-token",
    async (route) => {
      if (route.request().method() === "POST") {
        configured = true;
        return route.fulfill({
          status: 201,
          json: { token: "test-only-registration-token" },
        });
      }
      return route.fulfill({ json: { configured, created_at: null } });
    },
  );
  await page.goto("/#system-updates");
  await page.getByRole("button", { name: "流水线登记接入" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "生成登记凭据" }).click();
  await expect(
    dialog.getByLabel("版本登记凭据", { exact: true }),
  ).toHaveAttribute("type", "password");
  await expect(dialog.getByLabel("版本登记凭据", { exact: true })).toHaveValue(
    "test-only-registration-token",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await dialog.getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "流水线登记接入" }).click();
  await expect(dialog.getByLabel("版本登记凭据", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    dialog.getByRole("button", { name: "更换登记凭据" }),
  ).toBeDisabled();
});

test("manual catalog sync retains bundled versions on failure and retries immediately", async ({
  page,
}) => {
  await fixture(page, {
    catalog: {
      source: "bundled",
      stale: true,
      last_success_at: null,
      sync_error: "network_error",
    },
  });
  let calls = 0;
  await page.route("**/api/v1/system-updates/catalog/sync", async (route) => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({});
    calls++;
    await route.fulfill({
      json: {
        releases,
        source: calls === 1 ? "bundled" : "remote",
        stale: calls === 1,
        sync_error: calls === 1 ? "timeout" : "",
        generated_at: "2026-11-06T00:00:00Z",
        last_success_at: calls === 1 ? null : "2026-11-06T02:00:00Z",
        last_attempt_at: "2026-11-06T02:00:00Z",
        next_sync_at: null,
      },
    });
  });
  await page.goto("/#system-updates");
  await expect(page.getByText("镜像内置清单", { exact: true })).toBeVisible();
  await expect(
    page.getByText("尚未完成在线同步", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("目标版本", { exact: true }).selectOption("v1.16.0");
  await page.getByRole("button", { name: "立即同步清单" }).click();
  await expect(page.getByRole("alert")).toContainText("同步超时");
  await expect(page.getByLabel("目标版本", { exact: true })).toHaveValue(
    "v1.16.0",
  );
  await expect(page.getByLabel("目标版本", { exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "立即同步清单" }).click();
  await expect(page.getByText("清单已同步", { exact: true })).toBeVisible();
  await expect(page.getByText("在线同步", { exact: true })).toBeVisible();
  expect(calls).toBe(2);
});

test("manual catalog sync disables repeated clicks and keeps cache on API failure", async ({
  page,
}) => {
  await fixture(page, { catalog: { source: "cache", stale: true } });
  let calls = 0;
  let complete: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    complete = resolve;
  });
  await page.route("**/api/v1/system-updates/catalog/sync", async (route) => {
    calls++;
    await gate;
    await route.fulfill({
      status: 503,
      json: { message: "管理服务暂时不可用" },
    });
  });
  await page.goto("/#system-updates");
  await page.getByLabel("目标版本", { exact: true }).selectOption("v1.16.0");
  await page.getByRole("button", { name: "立即同步清单" }).click();
  await expect(page.getByRole("button", { name: "正在同步…" })).toBeDisabled();
  complete!();
  await expect(page.getByRole("alert")).toContainText("管理服务暂时不可用");
  await expect(page.getByLabel("目标版本", { exact: true })).toHaveValue(
    "v1.16.0",
  );
  await expect(page.getByLabel("目标版本", { exact: true })).toBeEnabled();
  expect(calls).toBe(1);
});

test("empty offline catalog explains failure and cannot submit an update", async ({
  page,
}) => {
  await fixture(page, {
    catalog: {
      releases: [],
      source: "none",
      stale: true,
      last_success_at: null,
      sync_error: "invalid_manifest",
    },
  });
  await page.goto("/#system-updates");
  await expect(page.getByRole("alert")).toContainText("清单校验失败");
  await expect(
    page.getByText("暂无可用的兼容版本，请点击「立即同步清单」。"),
  ).toBeVisible();
  await expect(page.getByLabel("目标版本", { exact: true })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "审阅中心更新" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "立即同步清单" }),
  ).toBeEnabled();
});
async function fixture(
  page: Page,
  options: {
    empty?: boolean;
    failCatalog?: boolean;
    uncertain?: boolean;
    role?: string;
    jobs?: any[];
    catalog?: Record<string, unknown>;
  } = {},
) {
  const requests: any[] = [];
  const paths: string[] = [];
  let failed = !!options.failCatalog;
  let disconnected = !!options.uncertain;
  let jobs: any[] = options.jobs || [];
  let stateError = false;
  let nodeEnabled = true;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    paths.push(path);
    if (path === "/api/v1/me")
      return route.fulfill({ json: { role: options.role || "owner" } });
    if (path === "/api/v1/nodes")
      return route.fulfill({
        json: [
          {
            id: "n1",
            name: "生产一号",
            enabled: nodeEnabled,
            online: true,
            agent_version: "1.15.0",
          },
          {
            id: "n2",
            name: "未配置节点",
            enabled: true,
            online: true,
            agent_version: "1.14.0",
          },
        ],
      });
    if (
      path === "/api/v1/system-updates/catalog" ||
      path === "/api/v1/system-updates/catalog/sync"
    )
      return route.fulfill(
        failed
          ? { status: 503, json: { message: "官方版本目录暂时不可用" } }
          : {
              json: {
                releases,
                source: "remote",
                stale: false,
                sync_error: "",
                generated_at: "2026-11-06T00:00:00Z",
                last_success_at: "2026-11-06T01:00:00Z",
                last_attempt_at: "2026-11-06T01:00:00Z",
                next_sync_at: null,
                ...options.catalog,
              },
            },
      );
    if (path === "/api/v1/system-updates/enrollments") {
      requests.push(route.request().postDataJSON());
      return route.fulfill({
        status: 201,
        json: {
          enrollment_token: "ephemeral-secret",
          expires_at: "2026-11-06T10:00:00Z",
        },
      });
    }
    if (path === "/api/v1/system-updates/jobs") {
      const body = route.request().postDataJSON();
      requests.push(body);
      if (disconnected) {
        disconnected = false;
        return route.abort();
      }
      jobs = [
        {
          ...body,
          id: "job1",
          batch_id: "batch1",
          updater_id: body.updater_ids[0],
          kind: body.updater_ids[0] === "u-center" ? "center" : "node",
          target_id: "n1",
          from_version: "v1.15.0",
          status: "queued",
          stage: "waiting",
          created_at: "2026-11-06T00:00:00Z",
          updated_at: "2026-11-06T00:00:00Z",
        },
      ];
      return route.fulfill({ status: 201, json: jobs });
    }
    if (path.endsWith("/cancel")) {
      jobs = jobs.map((job) => ({ ...job, status: "cancelled" }));
      return route.fulfill({ json: jobs[0] });
    }
    if (path.endsWith("/resolve")) {
      jobs = jobs.map((job) => ({
        ...job,
        resolved_at: "2026-11-06T01:00:00Z",
      }));
      return route.fulfill({ json: jobs[0] });
    }
    if (path === "/api/v1/system-updates")
      if (stateError)
        return route.fulfill({ status: 503, json: { message: "维护中" } });
    if (path === "/api/v1/system-updates")
      return route.fulfill({
        json: {
          updaters: options.empty
            ? []
            : [updater("center", "center"), updater("n1")],
          jobs,
        },
      });
    return route.fulfill({ json: [] });
  });
  return {
    requests,
    paths,
    disableNode: () => {
      nodeEnabled = false;
    },
    disconnect: () => {
      stateError = true;
    },
    reconnect: () => {
      stateError = false;
    },
    recover: () => {
      failed = false;
    },
  };
}
async function selectNode(page: Page) {
  await page.getByLabel("目标版本", { exact: true }).selectOption("v1.16.0");
  await page.getByLabel("选择 生产一号", { exact: true }).check();
  await page.getByRole("button", { name: "审阅节点更新", exact: true }).click();
}
test("owner navigation shows versions and unconfigured existing nodes", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/#groups");
  await page.getByRole("link", { name: "系统更新", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "系统更新", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("未配置节点", { exact: true })).toBeVisible();
  await expect(page.getByText("等待更新服务自动接入")).toBeVisible();
  await expect(page.getByText("更新服务 v1.15.0").first()).toBeVisible();
});
test("setup explains automatic installation without another credential exchange", async ({
  page,
}) => {
  const f = await fixture(page, { empty: true });
  await page.goto("/#system-updates");
  await page
    .getByRole("button", { name: "更新说明 中心", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "随中心安装、升级自动配置",
  );
  await expect(page.getByRole("dialog")).toContainText(
    "sudo ctl updater setup --kind center",
  );
  await expect(page.getByRole("dialog")).not.toContainText("updater enroll");
  await expect(
    page.getByRole("button", { name: "生成一次性更新凭据" }),
  ).toHaveCount(0);
  expect(f.requests).toHaveLength(0);
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page
    .getByRole("button", { name: "更新说明 未配置节点", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "sudo ctl agent install",
  );
  await expect(page.getByRole("dialog")).toContainText("复用本机节点身份");
  expect(f.paths).not.toContain("/api/v1/system-updates/enrollments");
});
test("catalog error has explicit recovery and disabled update submission", async ({
  page,
}) => {
  const f = await fixture(page, { failCatalog: true });
  await page.goto("/#system-updates");
  await expect(page.getByRole("alert")).toContainText("官方版本目录暂时不可用");
  await expect(
    page.getByRole("button", { name: "审阅节点更新" }),
  ).toBeDisabled();
  f.recover();
  await page.getByRole("button", { name: "立即同步清单" }).click();
  await expect(
    page.getByLabel("目标版本", { exact: true }).locator("option"),
  ).toHaveCount(2);
});
test("node batch defaults sequential and queued jobs can cancel", async ({
  page,
}) => {
  const f = await fixture(page);
  await page.goto("/#system-updates");
  await selectNode(page);
  await page.getByRole("button", { name: "确认节点更新", exact: true }).click();
  await expect(page.getByText("排队中", { exact: true })).toBeVisible();
  expect(f.requests[0]).toMatchObject({
    updater_ids: ["u-n1"],
    version: "v1.16.0",
    parallelism: 1,
    backup_confirmed: false,
  });
  await page.getByRole("button", { name: "取消排队任务" }).click();
  await expect(page.getByText("已取消", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "取消排队任务" })).toHaveCount(
    0,
  );
});
test("ambiguous submit retries frozen identity and locks selection", async ({
  page,
}) => {
  const f = await fixture(page, { uncertain: true });
  await page.goto("/#system-updates");
  await selectNode(page);
  await page.getByRole("button", { name: "确认节点更新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("结果尚未确认");
  await expect(page.getByLabel("目标版本", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "重试原请求", exact: true }).click();
  await expect(page.getByText("排队中", { exact: true })).toBeVisible();
  expect(f.requests[1]).toEqual(f.requests[0]);
});
test("center requires backup confirmation and explains outage and schema recovery", async ({
  page,
}) => {
  const f = await fixture(page);
  await page.goto("/#system-updates");
  await page.getByLabel("目标版本", { exact: true }).selectOption("v1.16.0");
  await page.getByRole("button", { name: "审阅中心更新" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("短暂中断");
  await expect(dialog).toContainText("自动回滚数据库结构");
  await expect(
    page.getByRole("button", { name: "确认中心更新" }),
  ).toBeDisabled();
  await page.getByLabel("我已安排镜像和物料备份，并接受维护中断").check();
  await page.getByRole("button", { name: "确认中心更新" }).click();
  await expect.poll(() => f.requests.length).toBe(1);
  expect(f.requests[0]).toMatchObject({
    updater_ids: ["u-center"],
    parallelism: 1,
    backup_confirmed: true,
  });
});
test("non-owner cannot enter update route or request update data", async ({
  page,
}) => {
  const f = await fixture(page, { role: "publisher" });
  await page.goto("/#system-updates");
  await expect(page.getByText("请使用管理员凭据登录。")).toBeVisible();
  expect(f.paths.some((path) => path.includes("system-updates"))).toBe(false);
  await expect(page.getByRole("link", { name: "系统更新" })).toHaveCount(0);
});
test("mobile setup and history fit the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto("/#system-updates");
  await expect(
    page.getByRole("heading", { name: "系统更新", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page
    .getByRole("button", { name: "更新说明 未配置节点", exact: true })
    .click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

const historyJob = (status: string, kind = "node") => ({
  id: "history1",
  request_id: "a".repeat(32),
  batch_id: "batch1",
  updater_id: kind === "node" ? "u-n1" : "u-center",
  kind,
  target_id: kind === "node" ? "n1" : "center",
  version: "v1.16.0",
  from_version: "v1.15.0",
  current_version: "v1.15.0",
  status,
  stage: "interrupted",
  result_code: "manual_recovery_required",
  resolved_at: null,
  created_at: "2026-11-06T00:00:00Z",
  updated_at: "2026-11-06T00:00:00Z",
  parallelism: 1,
});

test("polling stops on navigation and installation help makes no enrollment request", async ({
  page,
}) => {
  await page.clock.install();
  const f = await fixture(page);
  await page.goto("/#system-updates");
  await expect(page.getByRole("heading", { name: "更新目标" })).toBeVisible();
  const count = () =>
    f.paths.filter((path) => path === "/api/v1/system-updates").length;
  const initial = count();
  await page.clock.runFor(5100);
  await expect.poll(count).toBeGreaterThan(initial);
  await page
    .getByRole("button", { name: "更新说明 未配置节点", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("复用本机节点身份");
  expect(f.paths).not.toContain("/api/v1/system-updates/enrollments");
  await page.evaluate(() => {
    location.hash = "groups";
  });
  await expect(
    page.getByRole("heading", { name: "系统更新", exact: true }),
  ).toHaveCount(0);
  const stopped = count();
  await page.clock.runFor(15000);
  expect(count()).toBe(stopped);
  await page.getByRole("link", { name: "系统更新", exact: true }).click();
  await page
    .getByRole("button", { name: "更新说明 未配置节点", exact: true })
    .click();
  await expect(page.getByLabel("一次性更新凭据")).toHaveCount(0);
});

test("leased updates block duplicate targets and cannot cancel", async ({
  page,
}) => {
  await fixture(page, { jobs: [historyJob("leased")] });
  await page.goto("/#system-updates");
  await page.getByLabel("目标版本", { exact: true }).selectOption("v1.16.0");
  await expect(
    page.getByLabel("选择 生产一号", { exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "审阅中心更新" }),
  ).toBeDisabled();
  await expect(page.getByRole("button", { name: "取消排队任务" })).toHaveCount(
    0,
  );
});

test("manual recovery confirmation preserves history and unblocks repaired target", async ({
  page,
}) => {
  await fixture(page, { jobs: [historyJob("needs_attention")] });
  await page.goto("/#system-updates");
  await page.getByLabel("目标版本", { exact: true }).selectOption("v1.16.0");
  await expect(
    page.getByLabel("选择 生产一号", { exact: true }),
  ).toBeDisabled();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "确认已恢复" }).click();
  await expect(page.getByText("已确认恢复，历史结果保留。")).toBeVisible();
  await expect(page.getByText("需要人工处理", { exact: true })).toBeVisible();
  await expect(page.getByLabel("选择 生产一号", { exact: true })).toBeEnabled();
});

test("center outage shows reconnecting then recovers", async ({ page }) => {
  const f = await fixture(page, { jobs: [historyJob("running", "center")] });
  await page.goto("/#system-updates");
  await expect(page.getByText("执行中", { exact: true })).toBeVisible();
  f.disconnect();
  await page.getByRole("button", { name: "刷新状态" }).click();
  await expect(page.getByRole("alert")).toContainText("正在重新连接");
  f.reconnect();
  await page.getByRole("button", { name: "重试状态" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("mobile history remains readable with long task identifiers", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, {
    jobs: [{ ...historyJob("failed"), id: "long-job-".repeat(20) }],
  });
  await page.goto("/#system-updates");
  await expect(page.getByText("失败", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/system-updates-mobile.png",
    fullPage: true,
  });
});

test("selected node can be deselected after polling disables it", async ({
  page,
}) => {
  await page.clock.install();
  const f = await fixture(page);
  await page.goto("/#system-updates");
  await page.getByLabel("目标版本", { exact: true }).selectOption("v1.16.0");
  const checkbox = page.getByLabel("选择 生产一号", { exact: true });
  await checkbox.check();
  f.disableNode();
  await page.clock.runFor(5100);
  await expect(page.getByText("节点已禁用", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "审阅节点更新" }),
  ).toBeDisabled();
  await expect(checkbox).toBeEnabled();
  await checkbox.uncheck();
  await expect(checkbox).toBeDisabled();
});

test("uncertain original request survives navigation and reload", async ({
  page,
}) => {
  const f = await fixture(page, { uncertain: true });
  await page.goto("/#system-updates");
  await selectNode(page);
  await page.getByRole("button", { name: "确认节点更新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("结果尚未确认");
  await page.evaluate(() => {
    location.hash = "groups";
  });
  await page.getByRole("link", { name: "系统更新", exact: true }).click();
  await expect(page.getByRole("button", { name: "重试原请求" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "重试原请求" }).click();
  await expect(page.getByText("排队中", { exact: true })).toBeVisible();
  expect(f.requests[1]).toEqual(f.requests[0]);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("ctl:system-updates:pending-request"),
    ),
  ).toBe(null);
});

test("returning page reconciles accepted original request without a second POST", async ({
  page,
}) => {
  const request = {
    request_id: "a".repeat(32),
    updater_ids: ["u-n1"],
    version: "v1.16.0",
    parallelism: 1,
    backup_confirmed: false,
  };
  const f = await fixture(page, {
    jobs: [{ ...historyJob("succeeded"), request_id: request.request_id }],
  });
  await page.goto("/#groups");
  await page.evaluate(
    (value) =>
      sessionStorage.setItem(
        "ctl:system-updates:pending-request",
        JSON.stringify(value),
      ),
    request,
  );
  await page.goto("/#system-updates");
  await expect(
    page.getByText("已在历史记录中确认原请求的更新任务。"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "重试原请求" })).toHaveCount(0);
  expect(f.requests).toHaveLength(0);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("ctl:system-updates:pending-request"),
    ),
  ).toBe(null);
});

test("persisted request stays dormant for non-owner identity", async ({
  page,
}) => {
  const f = await fixture(page, { role: "publisher" });
  await page.goto("/#groups");
  await page.evaluate(() =>
    sessionStorage.setItem(
      "ctl:system-updates:pending-request",
      JSON.stringify({
        request_id: "b".repeat(32),
        updater_ids: ["u-n1"],
        version: "v1.16.0",
        parallelism: 1,
        backup_confirmed: false,
      }),
    ),
  );
  await page.goto("/#system-updates");
  await expect(page.getByText("请使用管理员凭据登录。")).toBeVisible();
  expect(f.paths.some((path) => path.includes("system-updates"))).toBe(false);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("ctl:system-updates:pending-request"),
    ),
  ).not.toBe(null);
});

test("unavailable session storage prevents an unpersisted update POST", async ({
  page,
}) => {
  await page.addInitScript(() =>
    Object.defineProperty(window, "sessionStorage", {
      get() {
        throw new Error("unavailable");
      },
    }),
  );
  const f = await fixture(page);
  await page.goto("/#system-updates");
  await selectNode(page);
  await page.getByRole("button", { name: "确认节点更新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("浏览器无法保存");
  expect(f.requests).toHaveLength(0);
});
