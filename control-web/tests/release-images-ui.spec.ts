import { test, expect } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`multi-service release images appear in list and details at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const project = {
      slug: "notes",
      name: "Notes",
      group: "apps",
      default_environment: "prod",
      deployment_type: "docker",
    };
    const image = "ctl.example/notes@sha256:" + "a".repeat(64);
    const postgres = "ctl.example/notes@sha256:" + "b".repeat(64);
    const release = {
      id: "release",
      version: "v2.0.0",
      image: "",
      images: { api: image, postgres, restore: image },
      status: "published",
      size: 1200,
      created_at: "2026-11-16T09:00:00Z",
      sha256: "c".repeat(64),
    };
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        "/api/v1",
        "",
      );
      let json: unknown = [];
      if (path === "/me") json = { role: "owner" };
      if (path === "/groups") json = [{ slug: "apps", name: "Apps" }];
      if (path === "/projects") json = [project];
      if (path === "/projects/notes") json = project;
      if (path.endsWith("/environments")) json = ["prod"];
      if (path.endsWith("/releases"))
        json = [
          release,
          {
            ...release,
            id: "old",
            version: "v1.0.0",
            images: undefined,
            image: "legacy@sha256:old",
          },
        ];
      await route.fulfill({ json });
    });
    await page.goto("/#project/notes");
    await page.getByRole("tab", { name: "版本", exact: true }).click();
    const row = page.getByRole("row").filter({ hasText: "v2.0.0" });
    await expect(row.locator("dt")).toHaveText(["api", "postgres", "restore"]);
    await expect(row.locator("code[title]")).toHaveCount(3);
    await expect(
      page.getByText("legacy@sha256:old", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "v2.0.0", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.locator("dt")).toHaveText([
      "api",
      "postgres",
      "restore",
    ]);
    await expect(dialog.getByText(postgres, { exact: true })).toBeVisible();
    expect(
      await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth),
    ).toBe(true);
    await page.screenshot({
      path: test.info().outputPath(`release-images-${width}.png`),
    });
  });
}
