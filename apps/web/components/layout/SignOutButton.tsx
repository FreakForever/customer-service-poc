"use client";

import { signOut } from "next-auth/react";

export function SignOutButton() {
  return <button type="button" className="min-h-8 rounded border border-surface px-3 text-sm hover:bg-blue-600" onClick={() => void signOut({ callbackUrl: "/" })}>Sign out</button>;
}
