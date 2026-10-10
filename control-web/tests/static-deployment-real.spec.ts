import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("static project creation, release upload and directory inheritance through the real API", async ({
  page,
}) => {
  test.setTimeout(90000);
  const owner = process.env.CTL_OWNER_TOKEN;
  if (!owner) throw new Error("disposable owner fixture required");
  const slug = `static-${Date.now().toString(36)}`;
  await page.goto("/");
  await page.getByLabel("管理员凭据").fill(owner);
  await page.getByRole("button", { name: "进入管理台" }).click();
  await page.goto("/#group/default");
  await page.getByRole("button", { name: "注册项目", exact: true }).click();
  await page.getByLabel("项目标识").fill(slug);
  await page.getByLabel("项目名称").fill("静态验收");
  await page.getByLabel("部署类型").selectOption("static");
  await expect(page.getByLabel("镜像仓库", { exact: true })).toHaveCount(0);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "注册项目", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.goto(`/#project/${slug}`);
  await expect(page.getByRole("tab", { name: "镜像管理" })).toHaveCount(0);
  await page.getByRole("button", { name: "项目设置" }).click();
  await expect(page.getByLabel("部署类型")).toBeDisabled();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "登记版本", exact: true }).click();
  await expect(page.getByLabel("短期 pull Token")).toHaveCount(0);
  await page.getByLabel("版本号").fill("v1.0.0");
  await page
    .getByLabel("静态文件压缩包")
    .setInputFiles({
      name: "site.zip",
      mimeType: "application/zip",
      buffer: Buffer.from("invalid"),
    });
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "登记版本", exact: true })
    .click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("版本号")).toHaveValue("v1.0.0");
  const packageBytes = readFileSync(
    resolve("../tests/fixtures/static-archives/valid.zip"),
  );
  await page
    .getByLabel("静态文件压缩包")
    .setInputFiles({
      name: "site.zip",
      mimeType: "application/zip",
      buffer: packageBytes,
    });
  await page.getByLabel("来源提交（可选）").fill("a".repeat(40));
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "登记版本", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "v1.0.0", exact: true }),
  ).toBeVisible();
  const headers = { Origin: new URL(page.url()).origin };
  const gp = "/api/v1/groups/default/environments/prod";
  const old = await page.request.get(gp);
  const revision = old.ok() ? (await old.json()).revision : 0;
  expect(
    (
      await page.request.put(gp, {
        headers,
        data: {
          expected_revision: revision,
          deployment_defaults: { target_dir: "/var/www/inherited-static" },
          runtime_env: [
            { key: "DOCKER_ONLY", operation: "set", value: "hidden" },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  await page.getByRole("tab", { name: "环境配置", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "业务变量", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "安装参数", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText(/继承目录：/)).toContainText(
    "/var/www/inherited-static",
  );
  const save = async () => {
    await page.getByRole("button", { name: "审阅并保存" }).click();
    await page.getByRole("button", { name: "保存新修订" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("已保存修订");
  };
  await page
    .getByLabel("安装目录", { exact: true })
    .fill("/var/www/own-static");
  await page.getByLabel("目标版本").selectOption("v1.0.0");
  await save();
  const path = `/api/v1/projects/${slug}/environments/prod`;
  let config = await (await page.request.get(path)).json();
  expect(config.deployment_defaults.target_dir).toBe("/var/www/own-static");
  expect(config.target_version).toBe("v1.0.0");
  expect(config.inherited_runtime_env).toEqual([]);
  await page.getByLabel("安装目录", { exact: true }).fill("");
  await save();
  config = await (await page.request.get(path)).json();
  expect(config.deployment_defaults.target_dir || "").toBe("");
  expect(config.inherited_deployment_defaults.target_dir).toBe(
    "/var/www/inherited-static",
  );
  await page.screenshot({
    path: "test-results/static-environment.png",
    fullPage: true,
  });
  await page.goto("/#group/default/environments");
  await page.getByRole("tab", { name: "prod", exact: true }).click();
  await page.getByLabel("安装目录", { exact: true }).fill("/var/www/group-ui");
  await save();
  expect(
    (await (await page.request.get(gp)).json()).deployment_defaults.target_dir,
  ).toBe("/var/www/group-ui");
  await page.getByLabel("安装目录", { exact: true }).fill("");
  await save();
  expect(
    (await (await page.request.get(gp)).json()).deployment_defaults
      .target_dir || "",
  ).toBe("");
});
