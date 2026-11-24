/** Staff routes the browser may reach through the proxy. The API checks the role again. */
export function staffPath(parts: string[]): string | null {
  if (!parts.length || !["console", "admin", "analytics"].includes(parts[0])) return null;
  if (parts.some((p) => !/^[A-Za-z0-9._:~-]+$/.test(p) || /^\.+$/.test(p))) return null;
  return `/v1/${parts.join("/")}`;
}
