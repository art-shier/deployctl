export function tokenCoversProject(
  token: { project?: string; projects?: string[]; groups?: string[] },
  project: { slug: string; group?: string },
): boolean {
  return (
    token.project === project.slug ||
    !!token.projects?.includes(project.slug) ||
    !!token.groups?.includes(project.group || "default")
  );
}
export function tokenScopeLabel(token: {
  project?: string;
  projects?: string[];
  groups?: string[];
}): string {
  return [
    token.project,
    ...(token.projects || []),
    ...(token.groups || []).map((group) => `项目组 ${group}`),
  ]
    .filter(Boolean)
    .join("、");
}
