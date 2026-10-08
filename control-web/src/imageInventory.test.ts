import { describe, expect, it } from "vitest";
import { groupImages, filterImages } from "./imageInventory";

describe("repository image inventory", () => {
  it("counts unique content separately from aliases and keeps all release protections", () => {
    const groups = groupImages([
      {
        tag: "latest",
        digest: "sha256:a",
        media_type: "index",
        versions: ["v1.0.0"],
      },
      {
        tag: "v1",
        digest: "sha256:a",
        media_type: "index",
        versions: ["v1.0.0", "v1.1.0"],
      },
      {
        tag: "orphan",
        digest: "sha256:b",
        media_type: "manifest",
        versions: [],
      },
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].tags).toEqual(["latest", "v1"]);
    expect(groups[0].versions).toEqual(["v1.0.0", "v1.1.0"]);
    expect(filterImages(groups, "V1.1.0")).toHaveLength(1);
    expect(filterImages(groups, "sha256:b")[0].tags).toEqual(["orphan"]);
  });
  it("retains every returned page of tags without treating the first 100 as the total", () => {
    const groups = groupImages(
      Array.from({ length: 125 }, (_, n) => ({
        tag: `image-${n}`,
        digest: `sha256:${n}`,
        media_type: "manifest",
        versions: [],
      })),
    );
    expect(groups).toHaveLength(125);
    expect(filterImages(groups, "").slice(100)).toHaveLength(25);
  });
});
