"use client";

import { signIn, signOut } from "next-auth/react";

type Props = { callbackUrl: string; google: boolean; demo: boolean; signedIn: boolean };

export function SignInButtons({ callbackUrl, google, demo, signedIn }: Props) {
  const btn = "min-h-11 w-full rounded-card px-4 font-semibold";
  return (
    <div className="mt-4 flex flex-col gap-3">
      {google && <button type="button" className={`${btn} bg-blue text-surface hover:bg-blue-600`} onClick={() => void signIn("google", { callbackUrl })}>Sign in with Google</button>}
      {demo && (["agent", "admin"] as const).map((role) => (
        <button key={role} type="button" className={`${btn} border border-field text-blue hover:bg-sky`} onClick={() => void signIn("demo", { role, callbackUrl })}>
          Continue as demo {role}
        </button>
      ))}
      {!google && !demo && <p className="text-steel">Google sign-in isn&apos;t set up in this environment yet.</p>}
      {signedIn && <button type="button" className={`${btn} border border-field text-blue hover:bg-sky`} onClick={() => void signOut({ callbackUrl: "/signin" })}>Sign out of the current account</button>}
    </div>
  );
}
