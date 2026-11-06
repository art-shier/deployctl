import { describe, expect, it } from "vitest";
import { APIError } from "./api";
import {
  eligibility,
  isNewerRelease,
  UpdateSubmission,
  type Updater,
  type UpdateDraft,
  UPDATE_REQUEST_STORAGE_KEY,
} from "./systemUpdates";
const updater: Updater = {
  id: "u1",
  kind: "node",
  target_id: "n1",
  current_version: "1.15.0",
  helper_version: "1.15.0",
  protocol: 1,
  platform: "linux",
  ready: true,
  blocked_reason: "",
  last_seen: new Date().toISOString(),
  online: true,
};
const draft: UpdateDraft = {
  updater_ids: ["u1"],
  version: "v1.16.0",
  parallelism: 1,
  backup_confirmed: false,
};
describe("official compatible versions", () => {
  it("requires stable protocol-1 release strictly newer than current", () => {
    expect(isNewerRelease("v1.16.0", "1.15.0")).toBe(true);
    for (const version of [
      "v1.15.0",
      "v1.14.0",
      "v2.0.0",
      "v1.16.0-rc.1",
      "latest",
      "v01.16.0",
    ])
      expect(isNewerRelease(version, "1.15.0")).toBe(false);
    expect(isNewerRelease("v1.16.0", "")).toBe(false);
  });
  it("blocks offline, disabled, pending, unready, active and incompatible targets", () => {
    expect(eligibility(updater, "v1.16.0", [])).toBe("");
    for (const change of [
      { online: false },
      { ready: false },
      { protocol: 2 },
      { current_version: "1.16.0" },
    ])
      expect(eligibility({ ...updater, ...change }, "v1.16.0", [])).not.toBe(
        "",
      );
    expect(
      eligibility(updater, "v1.16.0", [
        { updater_id: "u1", status: "needs_attention" },
      ] as any),
    ).not.toBe("");
    expect(
      eligibility(updater, "v1.16.0", [], { enabled: false } as any),
    ).not.toBe("");
    expect(
      eligibility(updater, "v1.16.0", [], {
        enabled: true,
        pending_authorization: true,
      } as any),
    ).not.toBe("");
  });
  it("separates center maintenance from node batches and honors resolved recovery", () => {
    const job = { updater_id: "other", kind: "center", status: "running" };
    expect(eligibility(updater, "v1.16.0", [job] as any)).not.toBe("");
    expect(
      eligibility({ ...updater, kind: "center" }, "v1.16.0", [
        { ...job, kind: "node" },
      ] as any),
    ).not.toBe("");
    expect(
      eligibility(updater, "v1.16.0", [
        {
          ...job,
          status: "needs_attention",
          resolved_at: "2026-11-06T00:00:00Z",
        },
      ] as any),
    ).toBe("");
  });
});
describe("uncertain update submission", () => {
  it("persists only request fields and restores the same identity after remount", async () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => {
        values.set(key, value);
      },
      removeItem: (key: string) => {
        values.delete(key);
      },
    };
    const submission = new UpdateSubmission(storage);
    let original: unknown;
    await expect(
      submission.submit(
        { ...draft, enrollment_token: "must-not-persist" } as any,
        async (body) => {
          original = structuredClone(body);
          throw new APIError(503, "offline");
        },
      ),
    ).rejects.toThrow();
    expect(JSON.parse(values.get(UPDATE_REQUEST_STORAGE_KEY)!)).toEqual(
      original,
    );
    expect(values.get(UPDATE_REQUEST_STORAGE_KEY)).not.toContain(
      "must-not-persist",
    );
    const restored = new UpdateSubmission(storage);
    expect(restored.draft).toEqual(draft);
    await expect(
      restored.submit(draft, async () => {
        throw new APIError(401, "expired");
      }),
    ).rejects.toThrow();
    expect(values.has(UPDATE_REQUEST_STORAGE_KEY)).toBe(true);
    await restored.submit(draft, async (body) => {
      expect(body).toEqual(original);
      return [];
    });
    expect(values.has(UPDATE_REQUEST_STORAGE_KEY)).toBe(false);
  });
  it("rejects invalid persisted data, extra secret fields and malformed identities", () => {
    const valid = { ...draft, request_id: "a".repeat(32) };
    for (const value of [
      "{",
      JSON.stringify({ ...valid, token: "secret" }),
      JSON.stringify({ ...valid, updater_ids: ["u1", "u1"] }),
      JSON.stringify({ ...valid, request_id: "invalid" }),
      JSON.stringify({ ...valid, parallelism: 6 }),
      JSON.stringify({ ...valid, version: "latest" }),
    ]) {
      let stored: string | null = value;
      const submission = new UpdateSubmission({
        getItem: () => stored,
        setItem: () => {},
        removeItem: () => {
          stored = null;
        },
      });
      expect(submission.uncertain).toBe(false);
      expect(stored).toBe(null);
    }
  });
  it("reconciles only complete matching batch history without resubmitting", async () => {
    const submission = new UpdateSubmission();
    let request: any;
    await expect(
      submission.submit(draft, async (body) => {
        request = body;
        throw new APIError(0, "offline");
      }),
    ).rejects.toThrow();
    expect(
      submission.reconcile([
        {
          request_id: request.request_id,
          updater_id: "different",
          version: draft.version,
        },
      ] as any),
    ).toBe(false);
    expect(
      submission.reconcile([
        {
          request_id: request.request_id,
          updater_id: "u1",
          version: draft.version,
        },
      ] as any),
    ).toBe(true);
    expect(submission.uncertain).toBe(false);
  });
  it("freezes request identity and draft after connection failure until acknowledged", async () => {
    const submission = new UpdateSubmission();
    let first: unknown;
    await expect(
      submission.submit(draft, async (body) => {
        first = structuredClone(body);
        throw new APIError(0, "offline");
      }),
    ).rejects.toThrow("offline");
    expect(submission.uncertain).toBe(true);
    await expect(
      submission.submit({ ...draft, version: "v1.17.0" }, async () => []),
    ).rejects.toThrow("原请求");
    await submission.submit(draft, async (body) => {
      expect(body).toEqual(first);
      expect(body.request_id).toMatch(/^[a-f0-9]{32}$/);
      return [];
    });
    expect(submission.uncertain).toBe(false);
  });
  it("allows correcting a definitively rejected request and rejects double submission", async () => {
    const submission = new UpdateSubmission();
    await expect(
      submission.submit(draft, async () => {
        throw new APIError(409, "busy");
      }),
    ).rejects.toThrow("busy");
    expect(submission.uncertain).toBe(false);
    let resolve: (value: any) => void = () => {};
    const pending = submission.submit(
      draft,
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    await expect(submission.submit(draft, async () => [])).rejects.toThrow(
      "提交",
    );
    resolve([]);
    await pending;
  });
});
