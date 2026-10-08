export function tokenCoversProject(
  token: {
    project?: string;
    projects?: string[];
    groups?: string[];
    excluded_projects?: string[];
  },
  project: { slug: string; group?: string },
): boolean {
  return (
    !token.excluded_projects?.includes(project.slug) &&
    (token.project === project.slug ||
      !!token.projects?.includes(project.slug) ||
      !!token.groups?.includes(project.group || "default"))
  );
}
export function tokenScopeLabel(token: {
  project?: string;
  projects?: string[];
  groups?: string[];
  excluded_projects?: string[];
}): string {
  return [
    token.project,
    ...(token.projects || []),
    ...(token.groups || []).map((group) => `项目组 ${group}`),
    ...(token.excluded_projects || []).map((project) => `排除 ${project}`),
  ]
    .filter(Boolean)
    .join("、");
}
