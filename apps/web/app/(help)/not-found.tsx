import Link from "next/link";

export default function NotFound() {
  return <div className="mx-auto max-w-2xl px-4 py-16"><h1 className="text-3xl">Page not found</h1><p className="mt-3">It may have been removed. <Link className="text-blue underline" href="/search">Search the help centre</Link>.</p></div>;
}
