import { describe, expect, it } from "vitest";
import { tokenCoversProject } from "./tokenScope";
describe("credential visibility", () => {
  it("keeps legacy credentials confined after default grouping", () => {
    expect(
      tokenCoversProject(
        { project: "notes" },
        { slug: "other", group: "default" },
      ),
    ).toBe(false);
    expect(
      tokenCoversProject(
        { project: "notes" },
        { slug: "notes", group: "default" },
      ),
    ).toBe(true);
  });
  it("follows group movement while preserving explicitly selected projects", () => {
    const token = { groups: ["apps"], projects: ["notes"] };
    expect(tokenCoversProject(token, { slug: "config", group: "apps" })).toBe(
      true,
    );
    expect(
      tokenCoversProject(token, { slug: "config", group: "default" }),
    ).toBe(false);
    expect(tokenCoversProject(token, { slug: "notes", group: "default" })).toBe(
      true,
    );
    expect(tokenCoversProject({}, { slug: "other", group: "default" })).toBe(
      false,
    );
  });
});
