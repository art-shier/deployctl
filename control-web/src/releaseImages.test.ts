import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import { ReleaseImages } from "./ReleaseImages";

test("multi-service images take precedence over the legacy image", () => {
  const html = renderToStaticMarkup(
    createElement(ReleaseImages, {
      release: {
        image: "old-image",
        images: {
          api: "repo@sha256:abc",
          postgres: "postgres@sha256:def",
          restore: "repo@sha256:abc",
        },
      },
    }),
  );
  for (const name of ["api", "postgres", "restore"])
    expect(html).toContain(`<dt>${name}</dt>`);
  expect(html).toContain("postgres@sha256:def");
  expect(html).not.toContain("old-image");
  expect(html.match(/repo@sha256:abc/g)?.length).toBe(4);
});

test("legacy and empty releases have explicit display values", () => {
  expect(
    renderToStaticMarkup(
      createElement(ReleaseImages, {
        release: { image: "legacy@sha256:abc", images: {} },
      }),
    ),
  ).toContain("legacy@sha256:abc");
  expect(
    renderToStaticMarkup(
      createElement(ReleaseImages, { release: { image: "" } }),
    ),
  ).toContain("未记录镜像");
});
