import { expect, test, type Page } from "@playwright/test";

const stamp = "2026-10-09T00:00:00Z";
const groups = ["default", "apps", "empty"].map((slug) => ({
  slug,
  name: slug,
  description: "",
  created_at: stamp,
}));
const project = {
  slug: "notes",
  name: "Notes",
  group: "apps",
  description: "",
  repository: "",
  image_repository: "registry.test/notes",
  default_environment: "prod",
  created_at: stamp,
};
const token = {
  id: "p1",
  name: "发布服务器",
  role: "publisher",
  groups: ["apps"],
  projects: [],
  excluded_projects: [],
  environments: ["stage"],
  expires_at: "2027-01-01T00:00:00Z",
  revoked: false,
  created_at: stamp,
};

async function fixture(page: Page, failures: ("conflict" | "network")[] = []) {
  const state = {
    deletes: [] as string[],
    projectDeleted: false,
    deletedGroups: [] as string[],
    token,
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (route.request().method() === "DELETE") {
      state.deletes.push(path);
      const error = failures.shift();
      if (error === "network") {
        await route.abort("failed");
        return;
      }
      if (error === "conflict") {
        await route.fulfill({
          status: 409,
          json: { message: "项目组或项目状态已变化，请重试" },
        });
        return;
      }
      if (path === "/projects/notes") state.projectDeleted = true;
      else state.deletedGroups.push(path.split("/")[2]);
      await route.fulfill({ status: 204 });
      return;
    }
    let body: unknown = [];
    if (path === "/me") body = { role: "owner" };
    else if (path === "/groups")
      body = groups.filter((g) => !state.deletedGroups.includes(g.slug));
    else if (path === "/projects") body = state.projectDeleted ? [] : [project];
    else if (path === "/projects/notes") body = project;
    else if (path === "/tokens") body = [state.token];
    else if (path === "/tokens/p1" && route.request().method() === "PATCH")
      body = state.token = {
        ...state.token,
        ...route.request().postDataJSON(),
      };
    else if (path === "/tokens/p1/secret")
      body = { token: "ctl_disposable_publisher" };
    await route.fulfill({ json: body });
  });
  return state;
}

test("project removal requires exact slug and cancel never deletes", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/#project/notes");
  await page.getByRole("button", { name: "项目设置", exact: true }).click();
  await page.getByRole("button", { name: "移除项目", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "移除项目 · notes" });
  await expect(dialog).toContainText(
    "已有运行容器、镜像文件和审计保留，标识不能复用",
  );
  await expect(
    dialog.getByRole("button", { name: "确认移除项目", exact: true }),
  ).toBeDisabled();
  await dialog.getByLabel("输入项目标识确认").fill("NOTES");
  await dialog.getByLabel("输入项目标识确认").press("Enter");
  expect(state.deletes).toEqual([]);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.deletes).toEqual([]);
});

test("project deletion preserves confirmation through conflict and network failure then navigates", async ({
  page,
}) => {
  const state = await fixture(page, ["conflict", "network"]);
  await page.goto("/#project/notes");
  await page.getByRole("button", { name: "项目设置", exact: true }).click();
  await page.getByRole("button", { name: "移除项目", exact: true }).click();
  await page.getByLabel("输入项目标识确认").fill("notes");
  const submit = page.getByRole("button", {
    name: "确认移除项目",
    exact: true,
  });
  await submit.click();
  await expect(page.getByRole("alert")).toContainText("状态已变化");
  await expect(page.getByLabel("输入项目标识确认")).toHaveValue("notes");
  await submit.click();
  await expect(page.getByRole("alert")).toContainText("无法连接管理服务");
  await expect(page.getByLabel("输入项目标识确认")).toHaveValue("notes");
  await submit.click();
  await expect(page).toHaveURL(/#group\/apps$/);
  await expect(
    page.getByRole("link", { name: "打开项目 Notes", exact: true }),
  ).toHaveCount(0);
  expect(state.deletes).toEqual([
    "/projects/notes",
    "/projects/notes",
    "/projects/notes",
  ]);
});

test("empty group removal returns to active groups", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/#group/empty/settings");
  await page.getByRole("button", { name: "移除项目组", exact: true }).click();
  await page.getByLabel("输入项目组标识确认").fill("empty");
  await page
    .getByRole("button", { name: "确认移除项目组", exact: true })
    .click();
  await expect(page).toHaveURL(/#groups$/);
  await expect(
    page.getByRole("link", { name: "查看项目组 empty", exact: true }),
  ).toHaveCount(0);
  expect(state.deletes).toEqual(["/groups/empty"]);
});

test("nonempty and reserved default groups cannot be removed", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/#group/apps/settings");
  await expect(
    page.getByRole("button", { name: "移除项目组", exact: true }),
  ).toBeDisabled();
  await expect(page.getByText(/先移出或移除组内.*1.*项目/)).toBeVisible();
  await page.goto("/#group/default/settings");
  await expect(
    page.getByRole("button", { name: "移除项目组", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("default 是保留项目组，不能移除。", { exact: true }),
  ).toBeVisible();
  expect(state.deletes).toEqual([]);
});

test("publisher scope description and optional environment limits survive editing", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/#tokens");
  await expect(
    page.getByRole("cell", {
      name: "发布、项目管理与配置读写（授权范围内）",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "stage", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("允许的环境")).toHaveValue("stage");
  await expect(page.getByRole("dialog")).toContainText(
    "可管理授权项目并读取、修改环境配置",
  );
  await expect(page.getByRole("dialog")).not.toContainText(
    "不能读取生产环境配置",
  );
  await page.getByLabel("允许的环境").fill("");
  await page.getByRole("button", { name: "保存凭据", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.token.environments).toEqual([]);
  await expect(
    page.getByRole("cell", { name: "全部环境", exact: true }),
  ).toBeVisible();
});

test("removal confirmation fits mobile and keeps focusable cancel", async ({
  page,
}) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#group/empty/settings");
  await page.getByRole("button", { name: "移除项目组", exact: true }).click();
  await expect(page.getByLabel("输入项目组标识确认")).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/group-remove-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "取消", exact: true }).focus();
  await expect(
    page.getByRole("button", { name: "取消", exact: true }),
  ).toBeFocused();
});

test("archived credential scopes remain visible and removable without scope widening", async ({
  page,
}) => {
  const state = await fixture(page);
  state.token = {
    ...token,
    groups: ["apps", "old-group"],
    projects: ["old-project"],
    excluded_projects: ["old-exclusion"],
  };
  await page.goto("/#tokens");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("授权项目组 old-group")).toBeChecked();
  await expect(page.getByLabel("单独授权项目 old-project")).toBeChecked();
  await expect(page.getByRole("dialog")).toContainText("已移除");
  await page.getByLabel("授权项目组 old-group").click();
  await expect(page.getByLabel("授权项目组 old-group")).toHaveCount(0);
  await page.getByLabel("单独授权项目 old-project").click();
  await expect(page.getByLabel("单独授权项目 old-project")).toHaveCount(0);
  await page.getByLabel("排除项目 old-exclusion").click();
  await page.getByRole("button", { name: "保存凭据", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.token).toMatchObject({
    groups: ["apps"],
    projects: [],
    excluded_projects: [],
  });
});

test("maximum-length identifiers fit the mobile removal dialog", async ({
  page,
}) => {
  await fixture(page);
  const slug = "a".repeat(48);
  await page.route("**/api/v1/groups", (route) =>
    route.fulfill({
      json: [
        { slug, name: "长标识项目组", description: "", created_at: stamp },
      ],
    }),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#group/${slug}/settings`);
  await page.getByRole("button", { name: "移除项目组", exact: true }).click();
  const dialog = page.getByRole("dialog");
  expect(
    await dialog.evaluate((node) => node.scrollWidth - node.clientWidth),
  ).toBeLessThanOrEqual(1);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});

for (const initialRole of ["publisher", "deployer"]) {
  test(`switching ${initialRole} credentials between roles preserves environment restrictions`, async ({
    page,
  }) => {
    const state = await fixture(page);
    state.token = {
      ...token,
      role: initialRole,
      environments: ["prod", "stage"],
    };
    await page.goto("/#tokens");
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(page.getByLabel("允许的环境")).toHaveValue("prod,stage");
    if (initialRole === "publisher")
      await page
        .getByRole("dialog")
        .getByRole("combobox")
        .selectOption("deployer");
    await page
      .getByRole("dialog")
      .getByRole("combobox")
      .selectOption("publisher");
    await expect(page.getByLabel("允许的环境")).toHaveValue("prod,stage");
    await expect(page.getByRole("dialog")).toContainText("新项目初始环境");
    await page.getByRole("button", { name: "保存凭据", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(state.token).toMatchObject({
      role: "publisher",
      environments: ["prod", "stage"],
    });
  });
}
