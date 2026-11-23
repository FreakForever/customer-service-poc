import { getHealth } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Home() {
  const health = await getHealth();
  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: "48px 16px" }}>
      <h1 style={{ color: "var(--color-blue)" }}>Matsui Support</h1>
      <p>Customer service POC — help centre, chat and agent console arrive in later build steps.</p>
      <h2>Agent API</h2>
      <pre>{JSON.stringify(health, null, 2)}</pre>
    </main>
  );
}
