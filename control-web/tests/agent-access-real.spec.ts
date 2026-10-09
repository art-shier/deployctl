import { createHash } from "node:crypto";
import { test, expect } from "@playwright/test";

test("real Agent access packages match checksums and page exposes no credential endpoint", async ({
  page,
}, testInfo) => {
  const owner = process.env.CTL_OWNER_TOKEN;
  if (!owner) throw new Error("disposable owner fixture required");
  await page.goto("/#agent");
  await page.getByLabel("管理员凭据").fill(owner);
  await page.getByRole("button", { name: "进入管理台" }).click();
  const paths: string[] = [];
  page.on("request", (request) => {
    paths.push(new URL(request.url()).pathname);
  });
  await expect(
    page.getByRole("heading", { name: "Agent 接入", exact: true }),
  ).toBeVisible();
  const response = await page.request.get("/api/v1/agent-access");
  expect(response.status()).toBe(200);
  const info = await response.json();
  const zip = await page.request.get(info.skill_url);
  expect(zip.status()).toBe(200);
  const zipBytes = await zip.body();
  expect(createHash("sha256").update(zipBytes).digest("hex")).toBe(
    info.skill_sha256,
  );
  expect(zipBytes.subarray(0, 2).toString()).toBe("PK");
  expect(
    zipBytes.includes(Buffer.from("team-deploy/assets/deployctl.pyz")),
  ).toBe(true);
  expect(zipBytes.includes(Buffer.from("team-deploy/references/"))).toBe(true);
  const cli = await page.request.get(info.cli_url);
  expect(cli.status()).toBe(200);
  expect(
    createHash("sha256")
      .update(await cli.body())
      .digest("hex"),
  ).toBe(info.cli_sha256);
  const entry = await page.request.get(info.entry_url);
  expect(entry.status()).toBe(200);
  expect(await entry.text()).toContain("team-deploy");
  const prompt = page.getByRole("textbox", {
    name: "Agent 交接提示词",
    exact: true,
  });
  await expect(prompt).toHaveValue(new RegExp(info.skill_sha256));
  expect(await prompt.inputValue()).not.toContain(owner);
  expect(await prompt.inputValue()).not.toContain("ctl()");
  await expect(
    page.getByRole("textbox", {
      name: "Windows PowerShell CLI 命令",
      exact: true,
    }),
  ).toHaveValue(
    /python "\$env:USERPROFILE\/\.codex\/skills\/team-deploy\/assets\/deployctl.pyz" --version/,
  );
  expect(await prompt.inputValue()).toContain(info.server_url + info.skill_url);
  const login = await page
    .getByRole("textbox", { name: "登录命令", exact: true })
    .inputValue();
  if (info.server_url === "https://ctl.shier.art")
    expect(login).not.toContain("config set server");
  else expect(login).toContain(`ctl config set server '${info.server_url}'`);
  await expect(
    page.getByRole("link", { name: "下载完整 skill ZIP" }),
  ).toHaveAttribute("href", new URL(page.url()).origin + info.skill_url);
  for (const role of ["publisher", "deployer", "owner"])
    await expect(page.locator(".agent-roles")).toContainText(role);
  await page.screenshot({
    path: testInfo.outputPath("agent-desktop.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(prompt).toHaveValue(new RegExp(info.cli_sha256));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({
    path: testInfo.outputPath("agent-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(
    paths.some((path) => /\/tokens(?:\/|$)|\/secret(?:\/|$)/.test(path)),
  ).toBe(false);
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain(owner);
});
