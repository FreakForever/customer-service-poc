import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Raw HTML in documents is never rendered (react-markdown default). */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-mt">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        table: (p) => <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Table"><table>{p.children}</table></div>,
        h1: (p) => <p><strong>{p.children}</strong></p>, h2: (p) => <p><strong>{p.children}</strong></p>,
        img: () => null,
        a: (p) => <a href={p.href} rel="noopener noreferrer">{p.children}</a>,
      }}>{children}</ReactMarkdown>
    </div>
  );
}
