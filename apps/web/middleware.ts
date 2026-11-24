import { getToken } from "next-auth/jwt";
import { NextResponse, type NextRequest } from "next/server";

import { allows, requiredRole, roleOf } from "./lib/roles";

/** Role guard (NFR-SEC-01): not signed in → sign-in page; signed in without the role → 403. */
export async function middleware(req: NextRequest) {
  const need = requiredRole(req.nextUrl.pathname);
  if (!need) return NextResponse.next();
  const secret = process.env.NEXTAUTH_SECRET;
  const token = secret ? await getToken({ req, secret }) : null;
  const api = req.nextUrl.pathname.startsWith("/api/");
  if (!token) {
    if (api) return NextResponse.json({ title: "Sign in required", status: 401 }, { status: 401 });
    const url = new URL("/signin", req.nextUrl);
    url.searchParams.set("callbackUrl", req.nextUrl.pathname + req.nextUrl.search);
    return NextResponse.redirect(url);
  }
  if (!allows(roleOf(token, process.env), need)) {
    if (api) return NextResponse.json({ title: "Forbidden", status: 403 }, { status: 403 });
    return new NextResponse(
      '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>No access</title>'
      + '<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem"><h1>No access</h1>'
      + `<p>Your account doesn't have the ${need} role.</p><p><a href="/signin?error=AccessDenied">Use another account</a> · <a href="/">Help centre</a></p>`,
      { status: 403, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
  }
  return NextResponse.next();
}

export const config = {
  runtime: "nodejs",
  matcher: ["/console/:path*", "/admin/:path*", "/analytics/:path*", "/api/demo/:path*", "/api/staff/:path*"],
};
