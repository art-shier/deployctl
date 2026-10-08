export interface ImageTag {
  tag: string;
  digest: string;
  media_type: string;
  versions: string[];
}
export interface ImageGroup {
  digest: string;
  media_type: string;
  tags: string[];
  versions: string[];
}
export function groupImages(images: ImageTag[]): ImageGroup[] {
  const groups = new Map<string, ImageGroup>();
  for (const image of images) {
    let group = groups.get(image.digest);
    if (!group) {
      group = {
        digest: image.digest,
        media_type: image.media_type,
        tags: [],
        versions: [],
      };
      groups.set(image.digest, group);
    }
    if (!group.tags.includes(image.tag)) group.tags.push(image.tag);
    for (const version of image.versions ?? [])
      if (!group.versions.includes(version)) group.versions.push(version);
  }
  for (const group of groups.values()) {
    group.tags.sort();
    group.versions.sort();
  }
  return [...groups.values()].sort((a, b) =>
    a.tags[0].localeCompare(b.tags[0]),
  );
}
export function filterImages(
  images: ImageGroup[],
  search: string,
): ImageGroup[] {
  const query = search.trim().toLowerCase();
  return images.filter(
    (image) =>
      !query ||
      [image.digest, ...image.tags, ...image.versions].some((value) =>
        value.toLowerCase().includes(query),
      ),
  );
}
