// An unchanged group must be omitted: the server preserves its current membership.
export function projectFormPayload<T extends { group: string }>(
  draft: T,
  originalGroup?: string,
): Omit<T, "group"> & { group?: string } {
  const { group, ...metadata } = draft;
  if ("deployment_type" in draft && draft.deployment_type === "static")
    Object.assign(metadata, { image_repository: "" });
  return originalGroup !== undefined && group === originalGroup
    ? metadata
    : { ...metadata, group };
}
