import { useEffect, useState } from "react";

type DashboardSummary = {
  failed_payments: number;
  amount_recovered_paise: number;
  amount_at_risk_paise: number;
  active_actions: number;
};

export default function App() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/dashboard/summary")
      .then((r) => r.json())
      .then(setSummary)
      .catch((e) => setError(String(e)));
  }, []);

  const formatMoney = (paise: number) =>
    new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 0,
    }).format(paise / 100);

  return (
    <main className="min-h-screen bg-[#f4f1ea] text-[#1c2624]">
      <div className="mx-auto max-w-6xl px-6 py-8 lg:px-10">
        <header className="flex items-end justify-between border-b border-[#c9c6bc] pb-8">
          <div>
            <p className="mb-3 text-xs font-bold uppercase tracking-[0.25em] text-[#8a5a32]">
              Revenue operations / live view
            </p>
            <h1 className="font-serif text-5xl leading-none tracking-tight">RecoverAI</h1>
          </div>
          <div className="hidden text-right text-sm text-[#65706a] sm:block">
            <p>Razorpay subscriptions</p>
            <p className="mt-1 flex items-center justify-end gap-2 text-xs uppercase tracking-widest">
              <span className="h-2 w-2 rounded-full bg-[#4e8b70]" /> monitoring
            </p>
          </div>
        </header>

        <section className="py-12">
          <div className="mb-7 flex items-baseline justify-between">
            <h2 className="font-serif text-3xl">Recovery pulse</h2>
            <span className="text-xs uppercase tracking-widest text-[#8b9188]">Today</span>
          </div>
          {error && (
            <p className="border-l-2 border-[#a44635] bg-[#f9e6df] p-4 text-sm text-[#7d3326]">
              Dashboard unavailable: {error}
            </p>
          )}
          {!error && !summary && <p className="text-sm text-[#65706a]">Loading recovery data...</p>}
          {summary && (
            <div className="grid gap-px overflow-hidden border border-[#c9c6bc] bg-[#c9c6bc] sm:grid-cols-2 lg:grid-cols-4">
              <Metric label="Failed payments" value={summary.failed_payments.toLocaleString("en-IN")} detail="Needs a decision" />
              <Metric label="Recovered" value={formatMoney(summary.amount_recovered_paise)} detail="Confirmed revenue" accent />
              <Metric label="Still at risk" value={formatMoney(summary.amount_at_risk_paise)} detail="Requires attention" warning />
              <Metric label="Active actions" value={summary.active_actions.toLocaleString("en-IN")} detail="In the recovery queue" />
            </div>
          )}
        </section>

        <section className="grid gap-8 border-t border-[#c9c6bc] pt-8 lg:grid-cols-[1.4fr_1fr]">
          <div>
            <h3 className="mb-3 text-xs font-bold uppercase tracking-[0.2em] text-[#8a5a32]">Pipeline</h3>
            <p className="max-w-xl font-serif text-2xl leading-snug">
              Every intervention is bounded by policy, recorded before execution, and visible here.
            </p>
          </div>
          <div className="border-l border-[#c9c6bc] pl-6 text-sm leading-6 text-[#65706a]">
            The dashboard is connected to the aggregate recovery ledger. Detailed subscription and exception views will build on this same audit trail.
          </div>
        </section>
        </div>
    </main>
  );
      }

function Metric({ label, value, detail, accent, warning }: { label: string; value: string; detail: string; accent?: boolean; warning?: boolean }) {
  return (
    <article className="bg-[#fbfaf7] p-6">
      <p className="text-xs uppercase tracking-widest text-[#65706a]">{label}</p>
      <p className={`mt-8 font-serif text-3xl ${accent ? "text-[#367057]" : warning ? "text-[#a44635]" : ""}`}>{value}</p>
      <p className="mt-2 text-xs text-[#8b9188]">{detail}</p>
    </article>
  );
}
