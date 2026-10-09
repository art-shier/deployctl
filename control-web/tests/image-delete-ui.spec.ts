import { test, expect } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`referenced multiarch image can be deleted at width ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const digest = `sha256:${"a".repeat(64)}`;
    let deleted = false;
    let fail = true;
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        "/api/v1",
        "",
      );
      if (route.request().method() === "DELETE") {
        expect(path).toBe(`/projects/notes/images/${digest}`);
        if (fail) {
          fail = false;
          await route.fulfill({
            status: 503,
            json: { message: "镜像仓库暂时不可用" },
          });
        } else {
          deleted = true;
          await route.fulfill({ json: { ok: true } });
        }
        return;
      }
      let body: unknown = [];
      if (path === "/me") body = { role: "owner" };
      if (path === "/projects/notes")
        body = {
          slug: "notes",
          name: "Notes",
          group: "default",
          description: "",
          repository: "",
          image_repository: "ctl.shier.art/notes",
          default_environment: "prod",
          created_at: "2026-10-09T00:00:00Z",
        };
      if (path.endsWith("/images/capabilities"))
        body = { managed: true, max_archive_bytes: 2147483648 };
      if (path === "/projects/notes/images")
        body = deleted
          ? []
          : [
              {
                tag: "v0.3.2",
                digest,
                media_type: "application/vnd.oci.image.index.v1+json",
                versions: ["v0.3.2"],
              },
              {
                tag: "stable",
                digest,
                media_type: "application/vnd.oci.image.index.v1+json",
                versions: ["v0.3.2"],
              },
            ];
      await route.fulfill({ json: body });
    });
    await page.goto("/#project/notes");
    await page.getByRole("tab", { name: "镜像管理" }).click();
    const remove = page.getByRole("button", { name: /删除镜像/ });
    await expect(remove).toBeEnabled();
    await remove.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("v0.3.2");
    await expect(dialog).toContainText("stable");
    await expect(dialog).toContainText("安装或回滚");
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    expect(deleted).toBe(false);
    await remove.click();
    await page.getByRole("button", { name: "确认删除镜像" }).click();
    await expect(page.getByRole("alert")).toContainText("镜像仓库暂时不可用");
    await expect(page.getByTestId("image-count")).toHaveText("1");
    await remove.click();
    await page.getByRole("button", { name: "确认删除镜像" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("image-count")).toHaveText("0");
    expect(deleted).toBe(true);
  });
}
