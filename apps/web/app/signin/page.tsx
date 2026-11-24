import { HelpShell } from "@/components/layout/HelpShell";
import { googleConfigured, isSignedIn } from "@/lib/auth";
import { demoStaffEnabled } from "@/lib/roles";

import { SignInButtons } from "./SignInButtons";

export const metadata = { title: "Staff sign-in" };

type Props = { searchParams: Promise<{ callbackUrl?: string | string[]; error?: string | string[] }> };

export default async function SignIn({ searchParams }: Props) {
  const q = await searchParams;
  const cb = typeof q.callbackUrl === "string" && /^\/(?![/\\])/.test(q.callbackUrl) ? q.callbackUrl : "/console";
  const ready = Boolean(process.env.NEXTAUTH_SECRET);
  return (
    <HelpShell>
      <div className="mx-auto my-12 max-w-md px-4">
        <div className="rounded-card border border-line bg-surface p-6">
          <h1 className="text-2xl">Staff sign-in</h1>
          {q.error && (
            <p role="alert" className="mt-3 text-bad">
              {q.error === "AccessDenied" ? "This account doesn't have access." : "Sign-in failed. Please try again."}
            </p>
          )}
          <SignInButtons callbackUrl={cb} google={ready && googleConfigured()} demo={ready && demoStaffEnabled(process.env)}
            signedIn={await isSignedIn()} />
        </div>
      </div>
    </HelpShell>
  );
}
