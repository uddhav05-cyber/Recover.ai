import { useEffect, useState } from "react";

type Health = {
  status: string;
  app_env?: string;
  database?: { connected: boolean; error?: string };
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  const connected = health?.database?.connected ?? false;

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 p-8 text-slate-100">
      <div className="w-full max-w-lg space-y-6">
        <header className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight">RecoverAI</h1>
          <p className="text-slate-400">
            Autonomous Razorpay subscription payment recovery. The recovery dashboard
            arrives in a later phase.
          </p>
        </header>

        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <h2 className="mb-2 text-sm font-medium text-slate-400">Backend health</h2>
          {error && <p className="text-sm text-red-400">Cannot reach backend: {error}</p>}
          {!error && !health && <p className="text-sm text-slate-500">Checking…</p>}
          {health && (
            <div className="flex items-center gap-2">
              <span
                className={`inline-block h-2.5 w-2.5 rounded-full ${
                  connected ? "bg-emerald-400" : "bg-amber-400"
                }`}
              />
              <span className="text-sm">
                status: <span className="font-mono">{health.status}</span> · db:{" "}
                <span className="font-mono">{connected ? "connected" : "down"}</span>
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
