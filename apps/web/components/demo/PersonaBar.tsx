"use client";

import { signIn, signOut } from "next-auth/react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { PERSONA_COOKIE, PERSONAS } from "@/lib/personas";
import type { Role } from "@/lib/roles";

type Props = { persona: string; staffRole: Role | null; staffLogin: boolean };

/** Demo bar (FR-UI-08). Staff personas are offered only where the demo staff login is enabled (local dev). */
export function PersonaBar({ persona, staffRole, staffLogin }: Props) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const value = staffLogin && staffRole ? `staff:${staffRole}` : persona;

  async function choose(v: string) {
    setNote("");
    if (v.startsWith("staff:")) {
      const role = v.slice(6);
      await signIn("demo", { role, callbackUrl: role === "admin" ? "/admin" : "/console" });
      return;
    }
    document.cookie = `${PERSONA_COOKIE}=${v}; path=/; SameSite=Lax${v ? "" : "; max-age=0"}`;
    if (staffLogin && staffRole) await signOut({ callbackUrl: "/" });
    else router.refresh();
  }

  async function sendEmails() {
    setBusy(true);
    setNote("Sending…");
    try {
      const r = await fetch("/api/demo/send-sample-emails", { method: "POST", headers: { "x-mcs-demo": "1" } });
      setNote(r.ok ? `${(await r.json()).count} sample emails queued` : "Couldn't queue the sample emails");
    } catch {
      setNote("Couldn't queue the sample emails");
    }
    setBusy(false);
  }

  const btn = "min-h-8 rounded border border-field bg-surface px-2 font-medium text-blue hover:bg-sky disabled:cursor-not-allowed disabled:text-steel";
  return (
    <section aria-label="Demo controls" className="border-b border-line bg-sky text-sm text-navy">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2">
        <label className="flex min-w-0 items-center gap-2">
          <span className="font-semibold">Demo persona</span>
          <select value={value} onChange={(e) => void choose(e.target.value)}
            className="min-h-8 min-w-0 max-w-52 rounded border border-field bg-surface px-2">
            <option value="">Anonymous visitor</option>
            <optgroup label="Customers">
              {PERSONAS.map((p) => <option key={p.id} value={p.id}>{p.company}</option>)}
            </optgroup>
            {staffLogin && (
              <optgroup label="Matsui staff">
                <option value="staff:agent">Agent</option>
                <option value="staff:admin">Admin</option>
              </optgroup>
            )}
          </select>
        </label>
        {staffRole === "admin" && (
          <>
            <button type="button" className={btn} disabled={busy} onClick={() => void sendEmails()}>Send sample emails</button>
            <button type="button" className={btn} disabled title="Arrives with the demo script (BS-33)">Reset demo</button>
          </>
        )}
        <span role="status" className="text-steel">{note}</span>
      </div>
    </section>
  );
}
