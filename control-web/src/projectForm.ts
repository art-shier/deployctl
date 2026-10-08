// An unchanged group must be omitted: the server preserves its current membership.
export function projectFormPayload<T extends { group: string }>(
  draft: T,
  originalGroup?: string,
): Omit<T, "group"> & { group?: string } {
  const { group, ...metadata } = draft;
  return originalGroup !== undefined && group === originalGroup
    ? metadata
    : { ...metadata, group };
}
