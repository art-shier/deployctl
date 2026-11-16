import React from "react";
import type { Release } from "./api";

export function ReleaseImages({
  release,
  compact = false,
}: {
  release: Pick<Release, "image" | "images">;
  compact?: boolean;
}) {
  const entries = Object.entries(release.images || {});
  if (!entries.length)
    return (
      <code
        className={compact ? "truncate" : "code-block"}
        title={release.image || undefined}
      >
        {release.image || "未记录镜像"}
      </code>
    );
  return (
    <dl
      className={"release-images" + (compact ? " release-images-compact" : "")}
    >
      {entries.map(([name, image]) => (
        <React.Fragment key={name}>
          <dt>{name}</dt>
          <dd>
            <code
              className={compact ? "truncate" : "stack-image"}
              title={image}
            >
              {image}
            </code>
          </dd>
        </React.Fragment>
      ))}
    </dl>
  );
}
