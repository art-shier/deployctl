import { test, expect, type Page } from "@playwright/test";

const stamp = "2026-10-08T12:00:00Z";
const group = {
  slug: "apps",
  name: "应用服务",
  description: "",
  created_at: stamp,
};
const project = {
  slug: "notes",
  name: "Notes",
  group: "apps",
  default_environment: "prod",
  created_at: stamp,
};
const credential = {
  id: "t1",
  name: "生产服务器",
  role: "deployer",
  groups: ["apps"],
  projects: [],
  excluded_projects: [],
  environments: ["prod"],
  expires_at: "2027-01-01T00:00:00Z",
  revoked: false,
  created_at: stamp,
};
async function fixture(page: Page) {
  let saved: unknown;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let body: unknown = [];
    if (path === "/me") body = { role: "owner" };
    else if (path === "/groups") body = [group];
    else if (path === "/projects") body = [project];
    else if (path === "/tokens") body = [credential];
    else if (path === "/tokens/t1/secret")
      body = { token: "ctl_reveal_fixture" };
    else if (path === "/tokens/t1") {
      saved = route.request().postDataJSON();
      body = { ...credential, ...(saved as object) };
    } else if (path.endsWith("/environments")) body = ["prod"];
    else if (path.endsWith("/environments/prod")) {
      const own = [
        { key: "TEXT", secret: false, configured: true, value: "own" },
      ];
      body = {
        id: "e1",
        environment: "prod",
        revision: 1,
        created_at: stamp,
        runtime_env: own,
        install_params: [],
        deployment_defaults: {},
        target_version: "stable",
        inherited_runtime_env: [
          { ...own[0], value: "group" },
          { key: "PASSWORD", secret: true, configured: true },
        ],
        inherited_install_params: [],
        group_source: { slug: "apps", id: "g1", revision: 2 },
      };
    } else if (path === "/projects/notes") body = project;
    await route.fulfill({ json: body });
  });
  return () => saved;
}

test("group environment uses shared editor without project deployment defaults", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/#group/apps");
  await page.getByRole("tab", { name: "环境配置", exact: true }).click();
  await expect(page.getByLabel("业务变量值 1")).toHaveValue("own");
  await expect(page.getByRole("heading", { name: "部署默认值" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("heading", { name: "安装目标" })).toHaveCount(0);
});

test("project removal restores inherited value while secrets remain masked", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/#project/notes");
  await page.getByRole("tab", { name: "环境配置", exact: true }).click();
  await expect(page.getByText("继承自项目组 apps").first()).toBeVisible();
  await expect(page.getByText("已配置 · 秘密值")).toBeVisible();
  await page.getByRole("button", { name: "删除 TEXT", exact: true }).click();
  await expect(
    page.locator(".inherited-variable").filter({ hasText: "TEXT" }),
  ).toContainText("group");
});

test("editing group credential excludes inherited project and reveal clears on close", async ({
  page,
}) => {
  const saved = await fixture(page);
  await page.goto("/#group/apps/access");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("允许项目 notes").uncheck();
  await expect(page.getByText("已排除").first()).toBeVisible();
  await page.getByRole("button", { name: "保存凭据", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(saved()).toMatchObject({
    groups: ["apps"],
    excluded_projects: ["notes"],
  });
  await page.getByRole("button", { name: "查看 Token", exact: true }).click();
  await expect(page.getByTestId("revealed-token")).toHaveText(
    "ctl_reveal_fixture",
  );
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByText("ctl_reveal_fixture")).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain("ctl_reveal_fixture");
});

test("unsaved environment drafts require confirmation only when dirty", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/#group/apps/environments");
  const dialogs: string[] = [];
  page.on("dialog", async (dialog) => {
    dialogs.push(dialog.message());
    await dialog.dismiss();
  });
  await page.getByRole("tab", { name: /组授权/ }).click();
  await expect(
    page.getByRole("heading", { name: "组授权", exact: true }),
  ).toBeVisible();
  expect(dialogs).toHaveLength(0);
  await page.getByRole("tab", { name: "环境配置", exact: true }).click();
  await page.getByLabel("业务变量值 1").fill("draft");
  await page.getByRole("tab", { name: /组授权/ }).click();
  await expect(page.getByLabel("业务变量值 1")).toHaveValue("draft");
  expect(dialogs).toHaveLength(1);
});

test("conflict keeps draft and hides secrets from review while group save excludes defaults", async ({
  page,
}) => {
  await fixture(page);
  await page.route("**/api/v1/groups/apps/environments/prod", async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      expect(body.deployment_defaults).toEqual({ target_dir: "" });
      expect(body).not.toHaveProperty("target_version");
      await route.fulfill({
        status: 409,
        json: { message: "服务器配置已变更" },
      });
    } else await route.fallback();
  });
  await page.goto("/#group/apps/environments");
  await page.getByLabel("业务变量值 1").fill("kept-draft");
  await page.getByRole("button", { name: "审阅并保存" }).click();
  await expect(page.getByRole("dialog")).not.toContainText("kept-draft");
  await page.getByRole("button", { name: "保存新修订" }).click();
  await expect(
    page.getByText("当前草稿已保留", { exact: false }),
  ).toBeVisible();
  await expect(page.getByLabel("业务变量值 1")).toHaveValue("kept-draft");
  await expect(page.getByRole("button", { name: "审阅并保存" })).toBeDisabled();
});

test("legacy token can rotate deliberately and revoked credentials stay hidden", async ({
  page,
}) => {
  await fixture(page);
  let rotations = 0;
  await page.route("**/api/v1/tokens", (route) =>
    route.fulfill({
      json: [
        credential,
        { ...credential, id: "revoked", name: "已撤销凭据", revoked: true },
      ],
    }),
  );
  await page.route("**/api/v1/tokens/t1/secret", (route) =>
    route.fulfill({
      status: 409,
      json: {
        message: "旧凭据未保存可恢复的Token，请重新生成Token后更新使用方。",
      },
    }),
  );
  await page.route("**/api/v1/tokens/t1/rotate", (route) => {
    rotations++;
    return route.fulfill({
      json: { token: "ctl_rotated_fixture", credential },
    });
  });
  await page.goto("/#tokens");
  await expect(page.getByText("已撤销凭据", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "查看 Token", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("旧凭据");
  expect(rotations).toBe(0);
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .getByRole("button", { name: "重新生成 Token", exact: true })
    .click();
  await expect(page.getByTestId("revealed-token")).toHaveText(
    "ctl_rotated_fixture",
  );
  expect(rotations).toBe(1);
  await page.evaluate(() => {
    location.hash = "groups";
  });
  await expect(page.getByText("ctl_rotated_fixture")).toHaveCount(0);
});

test("mobile group tabs and credential edit fit viewport", async ({ page }) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#group/apps/access");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("允许项目 notes")).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/credential-edit-mobile.png",
    fullPage: true,
  });
});
