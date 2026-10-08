import { expect, test } from "vitest";
import { projectFormPayload } from "./projectForm";
test("an unchanged group is omitted so stale metadata edits cannot restore access", () => {
  expect(
    projectFormPayload({ group: "apps", description: "updated" }, "apps"),
  ).toEqual({ description: "updated" });
  expect(
    projectFormPayload({ group: "default", description: "updated" }, "apps"),
  ).toEqual({ group: "default", description: "updated" });
  expect(projectFormPayload({ group: "default", description: "new" })).toEqual({
    group: "default",
    description: "new",
  });
});
