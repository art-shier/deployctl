import { expect, test } from "vitest";
import { APIError } from "./api";
import {
  DeploymentSubmission,
  deploymentPayload,
  findInstance,
  healthLabel,
  nodeLabel,
  taskLabel,
} from "./remote";

const draft = {
  node_id: "node-a",
  environment: "prod",
  action: "install" as const,
  version: "",
  host_port: "",
  bind_address: "",
};

test("uncertain creation retries the identical request and generates another id only after success", async () => {
  const submission = new DeploymentSubmission();
  const requests: unknown[] = [];
  const send = async (body: unknown) => {
    requests.push(body);
    if (requests.length === 1) throw new APIError(0, "offline");
    return { id: "task-a" };
  };
  await expect(submission.submit(draft, send)).rejects.toThrow("offline");
  expect(submission.uncertain).toBe(true);
  await expect(
    submission.submit({ ...draft, node_id: "node-b" }, send),
  ).rejects.toThrow("重试");
  await submission.submit(draft, send);
  expect(requests[0]).toEqual(requests[1]);
  expect((requests[0] as { request_id: string }).request_id).toMatch(
    /^[a-f0-9]{32}$/,
  );
  await submission.submit(draft, send);
  expect(requests[2]).not.toEqual(requests[1]);
});

test("definitive rejection releases request identity for corrected input", async () => {
  const submission = new DeploymentSubmission();
  await expect(
    submission.submit(draft, async () => {
      throw new APIError(409, "conflict");
    }),
  ).rejects.toThrow("conflict");
  expect(submission.uncertain).toBe(false);
  await expect(
    submission.submit(
      { ...draft, node_id: "node-b" },
      async (body) => body.node_id,
    ),
  ).resolves.toBe("node-b");
});

test("HTTP timeout retains the original identity because execution may have started", async () => {
  const submission = new DeploymentSubmission();
  let first = "";
  await expect(
    submission.submit(draft, async (body) => {
      first = body.request_id;
      throw new APIError(408, "timeout");
    }),
  ).rejects.toThrow("timeout");
  expect(submission.uncertain).toBe(true);
  await expect(
    submission.submit(draft, async (body) => body.request_id),
  ).resolves.toBe(first);
});

test("a second submission cannot dispatch while the first is in flight", async () => {
  const submission = new DeploymentSubmission();
  let finish!: (value: string) => void;
  let calls = 0;
  const first = submission.submit(draft, () => {
    calls++;
    return new Promise<string>((resolve) => {
      finish = resolve;
    });
  });
  await expect(
    submission.submit(draft, async () => {
      calls++;
      return "duplicate";
    }),
  ).rejects.toThrow("正在提交");
  finish("created");
  await expect(first).resolves.toBe("created");
  expect(calls).toBe(1);
});

test("deployment input validates port and excludes all overrides from rollback", () => {
  expect(() => deploymentPayload({ ...draft, host_port: "65536" })).toThrow(
    "端口",
  );
  expect(() => deploymentPayload({ ...draft, host_port: "1.2" })).toThrow(
    "端口",
  );
  expect(() => deploymentPayload({ ...draft, node_id: "" })).toThrow("节点");
  expect(
    deploymentPayload({
      ...draft,
      host_port: "8080",
      bind_address: " 127.0.0.1 ",
    }),
  ).toEqual({
    node_id: "node-a",
    environment: "prod",
    action: "install",
    host_port: 8080,
    bind_address: "127.0.0.1",
  });
  expect(
    deploymentPayload({
      ...draft,
      action: "rollback",
      version: "v2",
      host_port: "8080",
      bind_address: "0.0.0.0",
    }),
  ).toEqual({ node_id: "node-a", environment: "prod", action: "rollback" });
});

test("same project environment maps independently on two nodes", () => {
  const instances = [
    { id: "a", node_id: "node-a", environment: "prod" },
    { id: "b", node_id: "node-b", environment: "prod" },
  ];
  expect(findInstance(instances, "node-b", "prod")?.id).toBe("b");
});

test("offline and disabled nodes are distinct and stale health is explicitly historical", () => {
  expect(nodeLabel({ enabled: false, online: true })).toBe("已禁用");
  expect(nodeLabel({ enabled: true, online: false })).toBe("失联");
  expect(healthLabel({ health: "healthy", observed_at: null }, true)).toContain(
    "尚未观测",
  );
  expect(
    healthLabel(
      { health: "healthy", observed_at: "2020-01-01T00:00:00Z" },
      false,
    ),
  ).toBe("上次观测 · 健康");
  expect(taskLabel("running", false)).toContain("失联");
  expect(
    healthLabel(
      { health: "healthy", observed_at: "2020-01-01T00:00:00Z" },
      true,
    ),
  ).toBe("上次观测 · 健康");
  expect(
    healthLabel(
      { health: "unknown", observed_at: "2026-11-05T00:00:00Z" },
      true,
      Date.parse("2026-11-05T00:00:30Z"),
    ),
  ).toBe("最近观测 · 未知");
});
