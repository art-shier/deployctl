import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.restoreAllMocks());

it("accepts a successful deletion with no response body", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(null, { status: 204 }),
  );
  await expect(
    api<void>("/projects/notes", { method: "DELETE" }),
  ).resolves.toBeUndefined();
});

it("preserves a delete conflict message for recovery", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ message: "项目组仍有项目，请先移出" }), {
      status: 409,
    }),
  );
  await expect(
    api<void>("/groups/apps", { method: "DELETE" }),
  ).rejects.toMatchObject({ status: 409, message: "项目组仍有项目，请先移出" });
});
