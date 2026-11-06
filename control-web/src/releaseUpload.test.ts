import { expect, test } from "vitest";
import { hashReleaseFile, prepareReleaseForm } from "./releaseUpload";
import type { Project } from "./api";

const project = { slug: "static-a", deployment_type: "static" } as Project;
test("hashes slices without reading the whole file", async () => {
  const file = new File(["abc"], "site.zip");
  file.arrayBuffer = () => {
    throw new Error("whole file was buffered");
  };
  expect(await hashReleaseFile(file)).toBe(
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});
test("static delivery accepts an archive larger than the Docker limit", async () => {
  const file = new File([new Uint8Array(11 * 1024 * 1024)], "site.zip");
  const form = await prepareReleaseForm(
    project,
    file,
    "v1.0.0",
    "stable",
    "a".repeat(40),
  );
  expect(form.get("version")).toBe("v1.0.0");
  expect(form.get("commit")).toBe("a".repeat(40));
  expect(form.get("channel")).toBe("stable");
  expect(String(form.get("sha256"))).toMatch(/^[a-f0-9]{64}$/);
  await expect(
    prepareReleaseForm(
      { ...project, deployment_type: "docker" },
      file,
      "v1.0.0",
      "stable",
    ),
  ).rejects.toThrow("10 MiB");
});
