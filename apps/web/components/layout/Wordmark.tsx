/** Text wordmark until the logo asset arrives (OD-001). */
export function Wordmark({ sub }: { sub: string }) {
  return <span className="text-lg font-semibold tracking-tight">Matsui <span className="font-normal">{sub}</span></span>;
}
