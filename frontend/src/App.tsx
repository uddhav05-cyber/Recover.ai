import { useEffect, useState } from "react";

type Metrics = { total_subscriptions: number; failed_events: number; recovered_count: number; recovery_rate: number; amount_recovered_paise: number; amount_at_risk_paise: number; funnel: Array<{ stage: string; count: number; amount_paise: number }> };
type Subscription = { id: string; razorpay_subscription_id: string; status: string | null; amount_paise: number; currency: string; recovery_status: string; updated_at: string };
type Exception = { id: string; razorpay_subscription_id: string; category: string; outcome: string; amount_at_risk_paise: number; last_action_detail: string };

const money = (paise: number) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(paise / 100);
async function getJson<T>(url: string): Promise<T> { const response = await fetch(url); if (!response.ok) throw new Error(`Request failed (${response.status})`); return response.json() as Promise<T>; }

export default function App() {
  const [email, setEmail] = useState(sessionStorage.getItem("recoverai_email") ?? "demo@recover.ai");
  const [loggedIn, setLoggedIn] = useState(Boolean(sessionStorage.getItem("recoverai_token")));
  const [password, setPassword] = useState("demo");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [subscriptionPage, setSubscriptionPage] = useState(0);
  const [exceptionPage, setExceptionPage] = useState(0);
  const [exceptionCategory, setExceptionCategory] = useState("");
  const [exceptionOutcome, setExceptionOutcome] = useState("");
  const [subscriptionTotal, setSubscriptionTotal] = useState(0);
  const [exceptionTotal, setExceptionTotal] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pageSize = 8;

  useEffect(() => {
    if (!loggedIn) return;
    const load = async () => {
      try {
        const [nextMetrics, nextSubscriptions, nextExceptions] = await Promise.all([
          getJson<Metrics>("/api/recovery-metrics"),
          getJson<{ items: Subscription[]; total: number }>(`/api/subscriptions?skip=${subscriptionPage * pageSize}&limit=${pageSize}`),
          getJson<{ items: Exception[]; total: number }>(`/api/exceptions?skip=${exceptionPage * pageSize}&limit=${pageSize}${exceptionCategory ? `&category=${exceptionCategory}` : ""}${exceptionOutcome ? `&outcome=${exceptionOutcome}` : ""}`),
        ]);
        setMetrics(nextMetrics); setSubscriptions(nextSubscriptions.items); setSubscriptionTotal(nextSubscriptions.total);
        setExceptions(nextExceptions.items); setExceptionTotal(nextExceptions.total); setUpdatedAt(new Date()); setError(null);
      } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to load dashboard"); }
    };
    void load(); const interval = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(interval);
  }, [loggedIn, subscriptionPage, exceptionPage, exceptionCategory, exceptionOutcome]);

  const login = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const response = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
      if (!response.ok) throw new Error("Login failed");
      const data = (await response.json()) as { access_token: string };
      sessionStorage.setItem("recoverai_token", data.access_token); sessionStorage.setItem("recoverai_email", email);
      localStorage.removeItem("recoverai_token"); localStorage.removeItem("recoverai_email"); setLoggedIn(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Login failed"); }
  };

  if (!loggedIn) return <main className="flex min-h-screen items-center justify-center bg-[#f4f1ea] px-6 text-[#1c2624]"><form onSubmit={login} className="w-full max-w-sm border border-[#c9c6bc] bg-[#fbfaf7] p-8"><p className="text-xs font-bold uppercase tracking-[0.25em] text-[#8a5a32]">Revenue operations</p><h1 className="mt-4 font-serif text-5xl">RecoverAI</h1><p className="mt-3 text-sm leading-6 text-[#65706a]">Sign in to monitor bounded payment recovery actions.</p><label className="mt-8 block text-xs font-bold uppercase tracking-widest text-[#65706a]">Email<input className="mt-2 w-full border border-[#c9c6bc] bg-transparent p-3 text-sm outline-none focus:border-[#367057]" value={email} onChange={(event) => setEmail(event.target.value)} type="email" required /></label><label className="mt-4 block text-xs font-bold uppercase tracking-widest text-[#65706a]">Password<input className="mt-2 w-full border border-[#c9c6bc] bg-transparent p-3 text-sm outline-none focus:border-[#367057]" value={password} onChange={(event) => setPassword(event.target.value)} type="password" required /></label><button className="mt-6 w-full bg-[#1c2624] p-3 text-xs font-bold uppercase tracking-widest text-[#fbfaf7]" type="submit">Enter dashboard</button></form></main>;

  return <main className="min-h-screen bg-[#f4f1ea] text-[#1c2624]"><div className="mx-auto max-w-7xl px-5 py-7 lg:px-10"><header className="flex items-end justify-between border-b border-[#c9c6bc] pb-7"><div><p className="mb-3 text-xs font-bold uppercase tracking-[0.25em] text-[#8a5a32]">Revenue operations / live view</p><h1 className="font-serif text-5xl leading-none">RecoverAI</h1></div><div className="text-right text-sm text-[#65706a]"><p>{email}</p><p className="mt-2 flex items-center justify-end gap-2 text-xs uppercase tracking-widest"><span className="h-2 w-2 rounded-full bg-[#4e8b70]" /> monitoring</p></div></header>
  <section className="py-10"><div className="mb-6 flex items-baseline justify-between"><h2 className="font-serif text-3xl">Recovery pulse</h2><span className="text-xs uppercase tracking-widest text-[#8b9188]">{updatedAt ? `Updated ${updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Loading"}</span></div>{error && <p className="mb-5 border-l-2 border-[#a44635] bg-[#f9e6df] p-4 text-sm text-[#7d3326]">Dashboard unavailable: {error}</p>}<div className="grid gap-px overflow-hidden border border-[#c9c6bc] bg-[#c9c6bc] sm:grid-cols-2 lg:grid-cols-4"><Metric label="Subscriptions" value={metrics?.total_subscriptions.toLocaleString("en-IN") ?? "--"} detail="Tracked accounts" /><Metric label="Recovered" value={metrics ? money(metrics.amount_recovered_paise) : "--"} detail={metrics ? `${(metrics.recovery_rate * 100).toFixed(1)}% recovery rate` : "Confirmed revenue"} accent /><Metric label="Still at risk" value={metrics ? money(metrics.amount_at_risk_paise) : "--"} detail="Requires attention" warning /><Metric label="Failed events" value={metrics?.failed_events.toLocaleString("en-IN") ?? "--"} detail="Awaiting resolution" /></div></section>
  <section className="border-t border-[#c9c6bc] py-9"><div className="mb-6 flex items-baseline justify-between"><h2 className="font-serif text-3xl">Recovery funnel</h2><span className="text-xs uppercase tracking-widest text-[#8b9188]">All recorded events</span></div><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{metrics?.funnel.map((step) => <FunnelStep key={step.stage} label={step.stage} value={step.count} amount={step.amount_paise} accent={step.stage === "recovered"} />) ?? <p className="text-sm text-[#65706a]">Loading funnel...</p>}</div></section>
  <section className="grid gap-10 border-t border-[#c9c6bc] py-9 lg:grid-cols-[1.4fr_1fr]"><DataTable title="Subscriptions" page={subscriptionPage} total={subscriptionTotal} pageSize={pageSize} onPage={setSubscriptionPage} empty={!subscriptions.length}>{subscriptions.map((subscription) => <div className="grid grid-cols-[1.5fr_1fr_auto] items-center gap-3 border-b border-[#dedbd2] py-4 text-sm" key={subscription.id}><span className="font-mono text-xs">{subscription.razorpay_subscription_id}</span><Status status={subscription.recovery_status || subscription.status || "unknown"} /><span className="text-right text-[#65706a]">{money(subscription.amount_paise)}</span></div>)}</DataTable><div><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h3 className="text-xs font-bold uppercase tracking-[0.2em] text-[#8a5a32]">Exceptions</h3><div className="flex gap-2"><select aria-label="Filter exception category" value={exceptionCategory} onChange={(event) => { setExceptionCategory(event.target.value); setExceptionPage(0); }} className="border border-[#c9c6bc] bg-transparent px-2 py-1 text-xs text-[#65706a]"><option value="">All causes</option><option value="insufficient_funds">Insufficient funds</option><option value="expired_or_invalid_card">Invalid card</option><option value="bank_or_gateway_error">Bank error</option><option value="mandate_revoked">Mandate revoked</option><option value="other">Other</option></select><select aria-label="Filter exception outcome" value={exceptionOutcome} onChange={(event) => { setExceptionOutcome(event.target.value); setExceptionPage(0); }} className="border border-[#c9c6bc] bg-transparent px-2 py-1 text-xs text-[#65706a]"><option value="">Open cases</option><option value="still_at_risk">Still at risk</option><option value="escalated">Escalated</option></select></div></div><DataTable title="" page={exceptionPage} total={exceptionTotal} pageSize={pageSize} onPage={setExceptionPage} empty={!exceptions.length}>{exceptions.map((exception) => <div className="border-b border-[#dedbd2] py-4" key={exception.id}><div className="flex items-center justify-between gap-3 text-sm"><span className="font-mono text-xs">{exception.razorpay_subscription_id}</span><Status status={exception.outcome} /></div><div className="mt-2 flex justify-between gap-3 text-xs text-[#a44635]"><span>{exception.category.replaceAll("_", " ")}</span><span>{money(exception.amount_at_risk_paise)} at risk</span></div><p className="mt-2 truncate text-xs text-[#65706a]">{exception.last_action_detail}</p></div>)}</DataTable></div></section><footer className="border-t border-[#c9c6bc] pt-7 text-sm text-[#65706a]">Every intervention is policy-gated, recorded before execution, and visible in this recovery ledger.</footer></div></main>;
}

function Metric({ label, value, detail, accent, warning }: { label: string; value: string; detail: string; accent?: boolean; warning?: boolean }) { return <article className="bg-[#fbfaf7] p-6"><p className="text-xs uppercase tracking-widest text-[#65706a]">{label}</p><p className={`mt-8 font-serif text-3xl ${accent ? "text-[#367057]" : warning ? "text-[#a44635]" : ""}`}>{value}</p><p className="mt-2 text-xs text-[#8b9188]">{detail}</p></article>; }
function FunnelStep({ label, value, amount, accent }: { label: string; value: number; amount: number; accent?: boolean }) { return <div className="border-l-2 border-[#c9c6bc] bg-[#fbfaf7] p-5"><p className="text-xs uppercase tracking-widest text-[#65706a]">{label.replaceAll("_", " ")}</p><p className={`mt-5 font-serif text-4xl ${accent ? "text-[#367057]" : ""}`}>{value.toLocaleString("en-IN")}</p>{amount > 0 && <p className="mt-2 text-xs text-[#8b9188]">{money(amount)}</p>}</div>; }
function DataTable({ title, empty, children, page, total, pageSize, onPage }: { title: string; empty: boolean; children: React.ReactNode; page: number; total: number; pageSize: number; onPage: (page: number) => void }) { const pages = Math.max(1, Math.ceil(total / pageSize)); return <div><div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-bold uppercase tracking-[0.2em] text-[#8a5a32]">{title}</h3><span className="text-xs text-[#8b9188]">{total} total</span></div>{empty ? <p className="border border-dashed border-[#c9c6bc] p-5 text-sm text-[#65706a]">Nothing recorded yet.</p> : <>{children}<div className="mt-4 flex items-center justify-between text-xs text-[#65706a]"><button disabled={page === 0} onClick={() => onPage(page - 1)} className="disabled:opacity-30" type="button">Previous</button><span>{page + 1} / {pages}</span><button disabled={page + 1 >= pages} onClick={() => onPage(page + 1)} className="disabled:opacity-30" type="button">Next</button></div></>}</div>; }
function Status({ status }: { status: string }) { return <span className="text-xs uppercase tracking-wider text-[#65706a]">{status.replaceAll("_", " ")}</span>; }
