import { useEffect, useState } from "react";
import type { ReactNode } from "react";

type DashboardSummary = {
  failed_payments: number;
  amount_recovered_paise: number;
  amount_at_risk_paise: number;
  active_actions: number;
};

type DashboardOverview = {
  funnel: { failed: number; actioned: number; recovered: number };
  subscriptions: Array<{
    id: string;
    razorpay_subscription_id: string;
    status: string;
    amount_paise: number;
    updated_at: string;
  }>;
  exceptions: Array<{
    id: string;
    subscription_id: string;
    outcome: string;
    amount_at_risk_paise: number;
    resolved_at: string | null;
  }>;
};

export default function App() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/api/dashboard/summary").then((r) => r.json()),
      fetch("/api/dashboard/overview").then((r) => r.json()),
    ])
      .then(([nextSummary, nextOverview]) => {
        setSummary(nextSummary);
        setOverview(nextOverview);
      })
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

        {overview && <>
          <section className="border-t border-[#c9c6bc] py-10">
            <div className="mb-6 flex items-baseline justify-between">
              <h2 className="font-serif text-3xl">Recovery funnel</h2>
              <span className="text-xs uppercase tracking-widest text-[#8b9188]">All recorded events</span>
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <FunnelStep label="Failed" value={overview.funnel.failed} />
              <FunnelStep label="Actioned" value={overview.funnel.actioned} />
              <FunnelStep label="Recovered" value={overview.funnel.recovered} accent />
            </div>
          </section>

          <section className="grid gap-10 border-t border-[#c9c6bc] py-10 lg:grid-cols-[1.4fr_1fr]">
            <DataTable title="Subscriptions" empty={overview.subscriptions.length === 0}>
              {overview.subscriptions.map((subscription) => <div className="grid grid-cols-[1.4fr_1fr_auto] items-center gap-3 border-b border-[#dedbd2] py-4 text-sm" key={subscription.id}>
                <span className="font-mono text-xs">{subscription.razorpay_subscription_id}</span>
                <Status status={subscription.status} />
                <span className="text-right text-[#65706a]">{formatMoney(subscription.amount_paise)}</span>
              </div>)}
            </DataTable>
            <DataTable title="Exceptions" empty={overview.exceptions.length === 0}>
              {overview.exceptions.map((exception) => <div className="border-b border-[#dedbd2] py-4" key={exception.id}>
                <div className="flex items-center justify-between gap-3 text-sm"><span className="font-mono text-xs">{exception.subscription_id}</span><Status status={exception.outcome} /></div>
                <p className="mt-2 text-xs text-[#a44635]">{formatMoney(exception.amount_at_risk_paise)} at risk</p>
              </div>)}
            </DataTable>
          </section>
        </>}

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

function FunnelStep({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return <div className="border-l-2 border-[#c9c6bc] bg-[#fbfaf7] p-5"><p className="text-xs uppercase tracking-widest text-[#65706a]">{label}</p><p className={`mt-5 font-serif text-4xl ${accent ? "text-[#367057]" : ""}`}>{value.toLocaleString("en-IN")}</p></div>;
}

function DataTable({ title, empty, children }: { title: string; empty: boolean; children: ReactNode }) {
  return <div><div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-bold uppercase tracking-[0.2em] text-[#8a5a32]">{title}</h3><span className="text-xs text-[#8b9188]">Latest 20</span></div>{empty ? <p className="border border-dashed border-[#c9c6bc] p-5 text-sm text-[#65706a]">Nothing recorded yet.</p> : <div>{children}</div>}</div>;
}

function Status({ status }: { status: string }) {
  return <span className="text-xs uppercase tracking-wider text-[#65706a]">{status.replaceAll("_", " ")}</span>;
}
