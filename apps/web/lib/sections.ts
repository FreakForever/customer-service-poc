import type { Section } from "./kb";

export type Rendered = { anchor: string; headings: { level: number; text: string }[]; content: string };

/** Chunks carry their full heading path; show only the headings that changed (parts of one section share theirs). */
export function layoutSections(sections: Section[]): Rendered[] {
  let prev: string[] = [];
  return sections.map((s) => {
    let i = 0;
    while (i < s.heading_path.length && s.heading_path[i] === prev[i]) i++;
    const headings = s.heading_path.slice(i).map((text, k) => ({ level: Math.min(i + k + 1, 4), text })).filter((h) => h.level > 1);
    prev = s.heading_path;
    return { anchor: s.anchor, headings, content: s.content };
  });
}
