import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";

type Page = "overview" | "subscriptions" | "exceptions" | "audit" | "recovery";
type Metrics = {
  total_subscriptions: number;
  failed_events: number;
  recovered_count: number;
  recovery_rate: number;
  amount_recovered_paise: number;
  amount_at_risk_paise: number;
  funnel: { stage: string; count: number; amount_paise: number }[];
};
type Subscription = {
  id: string;
  razorpay_subscription_id: string;
  status: string | null;
  amount_paise: number;
  recovery_status: string;
  updated_at: string;
};
type Exception = {
  id: string;
  razorpay_subscription_id: string;
  category: string;
  outcome: string;
  amount_at_risk_paise: number;
  last_action_at?: string | null;
  last_action_detail: string;
};
type Audit = { id: number; event_time: string; actor: string; action: string; detail: Record<string, unknown> | null };
type TimelineState = "complete" | "failed" | "pending" | "in_progress";

const money = (paise: number) => new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", maximumFractionDigits: 0,
}).format(paise / 100);
const label = (value: string) => value.replaceAll("_", " ");
const formatDate = (value: string) => new Intl.DateTimeFormat("en-IN", {
  day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
}).format(new Date(value));
const request = async <T,>(url: string): Promise<T> => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<T>;
};

function Icon({ name }: { name: "grid" | "credit-card" | "alert" | "list" | "refresh" | "download" | "search" | "close" | "menu" | "arrow" }) {
  const paths: Record<string, string> = {
    grid: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    "credit-card": "M3 6h18v12H3zM3 10h18",
    alert: "M12 3 2.8 20h18.4L12 3zm0 6v5m0 3h.01",
    list: "M5 6h14M5 12h14M5 18h14M3 6h.01M3 12h.01M3 18h.01",
    refresh: "M20 11a8 8 0 0 0-14.9-3M4 5v4h4m-4 2a8 8 0 0 0 14.9 3M20 19v-4h-4",
    download: "M12 3v12m0 0 4-4m-4 4-4-4M4 20h16",
    search: "m20 20-4.5-4.5M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14Z",
    close: "M6 6l12 12M18 6 6 18",
    menu: "M4 6h16M4 12h16M4 18h16",
    arrow: "M5 12h14m-5-5 5 5-5 5",
  };
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

function Brand() {
  return <div className="brand"><span className="brand-symbol">R</span><span><strong>recover<span>.ai</span></strong><small>revenue operations</small></span></div>;
}

function Status({ status }: { status: string }) {
  const tone = status === "recovered" || status === "active" ? "success" : status === "still_at_risk" || status === "escalated" ? "warning" : "neutral";
  return <span className={`status status-${tone}`}><span className="status-dot" />{label(status)}</span>;
}

function EmptyState({ title = "Nothing to show", copy = "Records will appear here as your workspace receives data." }: { title?: string; copy?: string }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name="grid" /></span><strong>{title}</strong><p>{copy}</p></div>;
}

function PageIntro({ eyebrow, title, copy, action }: { eyebrow: string; title: string; copy: string; action?: ReactNode }) {
  return <div className="page-intro"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="intro-copy">{copy}</p></div>{action}</div>;
}

function MetricCard({ label: metricLabel, value, note, tone }: { label: string; value: string; note: string; tone?: "success" | "warning" }) {
  return <article className="metric-card"><span className="metric-label">{metricLabel}</span><strong className={tone ? `text-${tone}` : ""}>{value}</strong><small>{note}</small></article>;
}

function auditDetail(item: Audit, key: string): string | null {
  const value = item.detail?.[key];
  return value === undefined || value === null ? null : String(value);
}

function timelineState(item: Audit, outcome: string): TimelineState {
  if (item.action.endsWith(".recovered")) return "complete";
  if (item.action.endsWith(".failed") || item.action.includes("deny") || (outcome === "dead" && item.action.includes("mark_dead"))) return "failed";
  if (item.action.endsWith(".scheduled") || item.action.endsWith(".attempted")) return "in_progress";
  return "pending";
}

function timelineLabel(item: Audit): string {
  if (item.action === "diagnosis.recorded") return "Diagnosis recorded";
  if (item.action === "guardrail.allow") return "Guardrail approved action";
  if (item.action === "guardrail.deny") return "Guardrail denied action";
  if (item.action.endsWith(".scheduled")) return "Recovery scheduled";
  if (item.action.endsWith(".attempted")) return "Recovery attempted";
  if (item.action.endsWith(".recovered")) return "Payment recovered";
  if (item.action.endsWith(".failed")) return "Recovery attempt failed";
  return label(item.action);
}

function RecoveryDetails({ item, audit, auditLoading, onBack }: { item: Exception; audit: Audit[]; auditLoading: boolean; onBack: () => void }) {
  const attempts = audit.filter((event) => event.action.endsWith(".attempted") || event.action.endsWith(".failed") || event.action.endsWith(".recovered"));
  const firstEvent = audit[0]?.event_time;
  const lastEvent = audit[audit.length - 1]?.event_time;
  const recoveryType = audit.find((event) => event.action.startsWith("recovery."))?.action
    .replace("recovery.", "").replaceAll(".", " ") ?? "Not recorded";

  return <div className="recovery-details">
    <button className="back-button" onClick={onBack} type="button"><Icon name="arrow" /> Back to exceptions</button>
    <PageIntro eyebrow="Recovery operation" title={item.razorpay_subscription_id} copy="Inspect the recorded decision, execution attempts, and audit evidence for this recovery episode." action={<Status status={item.outcome} />} />
    <section className="recovery-summary panel">
      <div className="recovery-id-block"><span className="panel-kicker">Recovery ID</span><strong>{item.id}</strong><small>Outcome record from the recovery pipeline</small></div>
      <div className="summary-grid">
        <div><span>Status</span><Status status={item.outcome} /></div>
        <div><span>Recovery type</span><strong>{recoveryType}</strong></div>
        <div><span>Created</span><strong>{firstEvent ? formatDate(firstEvent) : "Not recorded"}</strong><small>{firstEvent ? "first associated audit event" : "not returned by the API"}</small></div>
        <div><span>Last recorded event</span><strong>{lastEvent ? formatDate(lastEvent) : "Not recorded"}</strong></div>
        <div><span>Root cause</span><strong>{label(item.category)}</strong></div>
        <div><span>Amount at risk</span><strong className="text-warning">{money(item.amount_at_risk_paise)}</strong></div>
      </div>
    </section>
    <div className="recovery-detail-grid">
      <section className="panel recovery-section"><div className="panel-heading"><div><p className="panel-kicker">Lifecycle</p><h2>Recovery timeline</h2></div><span className="muted-label">{audit.length} recorded events</span></div>{auditLoading ? <LoadingState /> : audit.length ? <div className="recovery-timeline">{audit.map((event) => <div className={`recovery-timeline-item timeline-${timelineState(event, item.outcome)}`} key={event.id}><span className="timeline-marker" /><div><div className="audit-item-head"><strong>{timelineLabel(event)}</strong><time>{formatDate(event.event_time)}</time></div><p>{event.actor}{event.detail ? ` · ${Object.entries(event.detail).map(([key, value]) => `${label(key)}: ${String(value)}`).join(", ")}` : ""}</p></div></div>)}</div> : <EmptyState title="No timeline events" copy="The API returned no audit events for this recovery." />}</section>
      <section className="panel recovery-section"><div className="panel-heading"><div><p className="panel-kicker">Execution</p><h2>Attempt history</h2></div></div>{auditLoading ? <LoadingState /> : attempts.length ? <div className="attempt-list">{attempts.map((attempt, index) => <div className="attempt-row" key={attempt.id}><div className={`attempt-number ${timelineState(attempt, item.outcome)}`}>{auditDetail(attempt, "attempt_number") ?? String(index + 1).padStart(2, "0")}</div><div><strong>{timelineLabel(attempt)}</strong><time>{formatDate(attempt.event_time)}</time><p>{auditDetail(attempt, "error") ?? auditDetail(attempt, "detail") ?? "No error detail recorded."}</p></div><Status status={timelineState(attempt, item.outcome) === "complete" ? "recovered" : timelineState(attempt, item.outcome) === "failed" ? "failed" : "pending"} /></div>)}</div> : <EmptyState title="No attempts recorded" copy="The current dashboard API does not expose an execution attempt for this recovery." />}</section>
    </div>
    <section className="panel recovery-section"><div className="panel-heading"><div><p className="panel-kicker">Traceability</p><h2>Event and audit history</h2></div></div><AuditList items={audit} /></section>
    {(item.outcome === "still_at_risk" || item.outcome === "escalated") && <div className="recovery-note" role="note"><strong>Why no retry action is shown</strong><span>Recovery actions are read-only in the current dashboard contract. No retry button is presented because the backend does not expose a safe retry endpoint.</span></div>}
  </div>;
}

function Overview({ metrics, exceptions, subscriptions, navigate }: { metrics: Metrics | null; exceptions: Exception[]; subscriptions: Subscription[]; navigate: (page: Page) => void }) {
  if (!metrics) return <LoadingState />;
  const maxFunnel = Math.max(...metrics.funnel.map((item) => item.count), 1);
  return <>
    <PageIntro eyebrow="Portfolio pulse" title="Recovery overview" copy="Monitor failed payments, recovery outcomes, and outstanding risk across your subscription portfolio." action={<span className="demo-badge"><span />Synthetic data · demo workspace</span>} />
    <section className="metric-grid">
      <MetricCard label="Subscriptions" value={metrics.total_subscriptions.toLocaleString("en-IN")} note="active portfolio" />
      <MetricCard label="Failed payment events" value={metrics.failed_events.toLocaleString("en-IN")} note="events received" tone="warning" />
      <MetricCard label="Recovered payments" value={metrics.recovered_count.toLocaleString("en-IN")} note={`${(metrics.recovery_rate * 100).toFixed(1)}% of failed events`} tone="success" />
      <MetricCard label="Amount recovered" value={money(metrics.amount_recovered_paise)} note="confirmed recovered" tone="success" />
      <MetricCard label="Recovery rate" value={`${(metrics.recovery_rate * 100).toFixed(1)}%`} note="of failed payment events" tone="success" />
      <MetricCard label="Amount at risk" value={money(metrics.amount_at_risk_paise)} note="unresolved outcomes" tone="warning" />
    </section>
    <div className="overview-grid">
      <section className="panel funnel-panel"><div className="panel-heading"><div><p className="panel-kicker">Decision pipeline</p><h2>Recovery funnel</h2></div><span className="muted-label">Current totals</span></div><div className="funnel">
        {metrics.funnel.map((item, index) => <div className="funnel-item" key={item.stage}><div className="funnel-meta"><span className={`funnel-index index-${index}`}>{String(index + 1).padStart(2, "0")}</span><span><strong>{label(item.stage)}</strong><small>{item.count} cases{item.amount_paise ? ` · ${money(item.amount_paise)}` : ""}</small></span></div><div className="funnel-track"><span style={{ width: `${Math.max((item.count / maxFunnel) * 100, item.count ? 8 : 0)}%` }} /></div><strong className="funnel-count">{item.count}</strong></div>)}
      </div></section>
      <section className="panel risk-panel"><div className="panel-heading"><div><p className="panel-kicker">Exposure</p><h2>At a glance</h2></div></div><div className="risk-block"><span className="risk-icon"><Icon name="alert" /></span><div><small>Amount at risk</small><strong>{money(metrics.amount_at_risk_paise)}</strong><p>Unresolved payment outcomes requiring attention.</p></div></div><div className="risk-rule" /><div className="risk-stat"><span>Recovery rate</span><strong>{(metrics.recovery_rate * 100).toFixed(1)}%</strong></div><div className="risk-stat"><span>Open exceptions</span><strong>{exceptions.length}</strong></div></section>
    </div>
    <section className="panel table-panel"><div className="panel-heading table-heading"><div><p className="panel-kicker">Needs attention</p><h2>Recent exceptions</h2></div><button className="text-button" onClick={() => navigate("exceptions")} type="button">View all <Icon name="arrow" /></button></div><ExceptionTable items={exceptions.slice(0, 5)} onOpen={() => navigate("exceptions")} compact /></section>
    <section className="panel table-panel"><div className="panel-heading table-heading"><div><p className="panel-kicker">Portfolio</p><h2>Recently updated subscriptions</h2></div><button className="text-button" onClick={() => navigate("subscriptions")} type="button">View all <Icon name="arrow" /></button></div><SubscriptionTable items={subscriptions.slice(0, 5)} /></section>
  </>;
}

function LoadingState() {
  return <div className="loading-state"><span className="spinner" /><p>Loading workspace data…</p></div>;
}

function SubscriptionTable({ items }: { items: Subscription[] }) {
  if (!items.length) return <EmptyState title="No subscriptions yet" copy="Subscriptions will appear when the first payment event is ingested." />;
  return <div className="data-table"><div className="table-row table-head"><span>Subscription</span><span>Status</span><span>Recovery state</span><span>Amount</span><span>Updated</span></div>{items.map((item) => <div className="table-row" key={item.id}><strong className="id-text">{item.razorpay_subscription_id}</strong><Status status={item.status ?? "unknown"} /><Status status={item.recovery_status} /><span>{money(item.amount_paise)}</span><time dateTime={item.updated_at}>{formatDate(item.updated_at)}</time></div>)}</div>;
}

function ExceptionTable({ items, onOpen, compact = false }: { items: Exception[]; onOpen: (item: Exception) => void; compact?: boolean }) {
  if (!items.length) return <EmptyState title="Queue is clear" copy="No unresolved exceptions match the current filters." />;
  return <div className="data-table exception-table"><div className="table-row table-head"><span>Subscription</span><span>Category</span><span>Outcome</span><span>Amount at risk</span><span>{compact ? "" : "Last action"}</span></div>{items.map((item) => <button className="table-row table-button" key={item.id} onClick={() => onOpen(item)} type="button"><strong className="id-text">{item.razorpay_subscription_id}</strong><span className="category-text">{label(item.category)}</span><Status status={item.outcome} /><strong>{money(item.amount_at_risk_paise)}</strong>{compact ? <span className="row-arrow"><Icon name="arrow" /></span> : <span className="action-text">{item.last_action_detail}</span>}</button>)}</div>;
}

function App() {
  const [email, setEmail] = useState(sessionStorage.getItem("recoverai_email") ?? "demo@recover.ai");
  const [loggedIn, setLoggedIn] = useState(Boolean(sessionStorage.getItem("recoverai_token")));
  const [password, setPassword] = useState("demo");
  const [page, setPage] = useState<Page>("overview");
  const [mobile, setMobile] = useState(false);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [selected, setSelected] = useState<Exception | null>(null);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [outcome, setOutcome] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loginLoading, setLoginLoading] = useState(false);

  const load = async (): Promise<boolean> => {
    setLoading(true);
    try {
      const filters = `${category ? `&category=${category}` : ""}${outcome ? `&outcome=${outcome}` : ""}`;
      const [m, s, e] = await Promise.all([
        request<Metrics>("/api/recovery-metrics"),
        request<{ items: Subscription[] }>("/api/subscriptions?skip=0&limit=100"),
        request<{ items: Exception[] }>(`/api/exceptions?skip=0&limit=200${filters}`),
      ]);
      setMetrics(m); setSubscriptions(s.items); setExceptions(e.items); setError(null);
      return true;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to load workspace data"); return false; }
    finally { setLoading(false); }
  };

  useEffect(() => { if (!loggedIn) return; void load(); const timer = window.setInterval(() => void load(), 30000); return () => window.clearInterval(timer); }, [loggedIn, category, outcome]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(null), 3000); return () => window.clearTimeout(timer); }, [toast]);

  const login = async (event: FormEvent) => {
    event.preventDefault();
    if (loginLoading) return;
    setError(null);
    setLoginLoading(true);
    try {
      const response = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
      if (!response.ok) throw new Error("Login failed. Check your credentials.");
      const data = (await response.json()) as { access_token: string };
      sessionStorage.setItem("recoverai_token", data.access_token); sessionStorage.setItem("recoverai_email", email); setLoggedIn(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Login failed"); }
    finally { setLoginLoading(false); }
  };
  const logout = () => { sessionStorage.clear(); setLoggedIn(false); };
  const navigate = (next: Page) => { setPage(next); setMobile(false); };
  const refresh = async () => {
    if (await load()) setToast("Workspace refreshed");
  };
  useEffect(() => {
    if (!selected) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setSelected(null); setPage("exceptions"); }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selected]);
  const openAudit = async (item: Exception) => {
    setSelected(item); setPage("recovery"); setAudit([]); setAuditLoading(true);
    try { const data = await request<{ items: Audit[] }>(`/api/exceptions/${item.id}/audit`); setAudit(data.items); }
    catch { setToast("Audit trail unavailable"); }
    finally { setAuditLoading(false); }
  };
  const exportCsv = () => {
    const rows = ["Subscription,Category,Outcome,Amount at risk,Last action", ...exceptions.map((item) => [item.razorpay_subscription_id, item.category, item.outcome, item.amount_at_risk_paise, item.last_action_detail].map((value) => `"${String(value).replaceAll('"', '""')}"`).join(","))];
    const blob = new Blob([rows.join("\n")], { type: "text/csv" }); const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "recoverai-exceptions.csv"; link.click(); URL.revokeObjectURL(link.href); setToast("Exception export downloaded");
  };
  const filteredSubscriptions = useMemo(() => subscriptions.filter((item) => (!status || item.status === status) && (!search || item.razorpay_subscription_id.toLowerCase().includes(search.toLowerCase()))), [subscriptions, search, status]);

  if (!loggedIn) return <main className="login-screen"><div className="login-aside"><Brand /><div><p className="eyebrow">Payment recovery, with guardrails</p><h1>Turn failed payments into <em>recoverable</em> decisions.</h1><p>Recover.ai diagnoses subscription failures, applies policy-approved actions, and keeps every decision auditable.</p></div><small>Built for accountable revenue operations</small></div><form className="login-panel" onSubmit={login}><div className="mobile-brand"><Brand /></div><p className="eyebrow">Welcome back</p><h2>Sign in to your workspace</h2><p className="login-copy">Use your workspace credentials to continue.</p><label>Email address<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" required disabled={loginLoading} /></label><label>Password<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" required disabled={loginLoading} /></label>{error && <p className="form-error" role="alert">{error}</p>}<button className="primary-button full-width" type="submit" disabled={loginLoading}>{loginLoading ? "Signing in…" : "Continue"} {!loginLoading && <Icon name="arrow" />}</button><p className="login-note">Demo workspace · authentication endpoint is a development stub</p></form></main>;

  const links: [Page, string, "grid" | "credit-card" | "alert" | "list"][] = [["overview", "Overview", "grid"], ["subscriptions", "Subscriptions", "credit-card"], ["exceptions", "Exceptions", "alert"], ["audit", "Audit trail", "list"]];
  const pageTitle = page === "overview" ? "Overview" : page === "subscriptions" ? "Subscriptions" : page === "exceptions" ? "Exceptions" : page === "recovery" ? "Recovery details" : "Audit trail";
  return <div className="app-shell"><div className={`sidebar-overlay ${mobile ? "visible" : ""}`} onClick={() => setMobile(false)} /><aside className={`sidebar ${mobile ? "sidebar-open" : ""}`}><Brand /><div className="workspace-switcher"><span className="workspace-avatar">A</span><span><strong>Acme Subscriptions</strong><small>Primary workspace</small></span><span className="chevron">⌄</span></div><nav aria-label="Main navigation">{links.map(([key, text, icon]) => <button className={`nav-item ${page === key ? "active" : ""}`} aria-current={page === key ? "page" : undefined} onClick={() => navigate(key)} key={key} type="button"><Icon name={icon} />{text}{key === "exceptions" && exceptions.length > 0 && <span className="nav-count">{exceptions.length}</span>}</button>)}</nav><div className="sidebar-bottom"><div className="guardrail-status"><span className="status-dot" /><span><strong>Guardrail policy</strong><small>Deterministic checks configured</small></span></div><div className="sidebar-caption">Demo workspace</div><button className="profile" onClick={logout} type="button"><span className="profile-avatar">{email[0].toUpperCase()}</span><span><strong>{email.split("@")[0]}</strong><small>Sign out</small></span><span className="profile-more">···</span></button></div></aside><div className="main-column"><header className="topbar"><button className="mobile-menu" onClick={() => setMobile(true)} type="button" aria-label="Open navigation"><Icon name="menu" /></button><div className="topbar-title"><p className="breadcrumb">Workspace <span>/</span> {pageTitle}</p><h2>{pageTitle}</h2></div><div className="topbar-actions"><button className="icon-button" onClick={() => { void refresh(); }} title="Refresh workspace" type="button" disabled={loading}><Icon name="refresh" /></button>{page === "exceptions" && <button className="secondary-button export-button" onClick={exportCsv} type="button"><Icon name="download" /> Export CSV</button>}</div></header><main className="content">{error && <div className="error-banner" role="alert">{error}<button onClick={() => void refresh()} type="button">Retry</button></div>}{page === "overview" && <Overview metrics={metrics} exceptions={exceptions} subscriptions={subscriptions} navigate={navigate} />}{page === "subscriptions" && <><PageIntro eyebrow="Billing portfolio" title="Subscriptions" copy="Review subscription status and the latest recovery state across your portfolio." /><section className="panel table-panel"><div className="toolbar"><label className="search-field"><Icon name="search" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search subscription ID" aria-label="Search subscription ID" /></label><select className="filter-control" value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter by status"><option value="">All statuses</option><option value="active">Active</option><option value="cancelled">Cancelled</option><option value="paused">Paused</option></select><span className="toolbar-count">{filteredSubscriptions.length} records</span></div><SubscriptionTable items={filteredSubscriptions} /></section></>}{page === "exceptions" && <><PageIntro eyebrow="Investigation queue" title="Exceptions" copy="Investigate unresolved payment outcomes before they become lost revenue." action={<button className="secondary-button export-button" onClick={exportCsv} type="button"><Icon name="download" /> Export CSV</button>} /><section className="panel table-panel"><div className="toolbar"><select className="filter-control" value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Filter by category"><option value="">All categories</option><option value="insufficient_funds">Insufficient funds</option><option value="expired_card">Expired card</option><option value="bank_server_error">Bank server error</option><option value="mandate_revoked">Mandate revoked</option><option value="other">Other</option></select><select className="filter-control" value={outcome} onChange={(event) => setOutcome(event.target.value)} aria-label="Filter by outcome"><option value="">All outcomes</option><option value="still_at_risk">Still at risk</option><option value="escalated">Escalated</option></select><span className="toolbar-count">{exceptions.length} open cases</span></div><ExceptionTable items={exceptions} onOpen={openAudit} /></section></>  }{page === "recovery" && selected && <RecoveryDetails item={selected} audit={audit} auditLoading={auditLoading} onBack={() => { setSelected(null); navigate("exceptions"); }} />}{page === "audit" && <AuditPage items={audit} selected={selected} />}</main><footer className="status-footer"><span><span className="status-dot" /> Data refreshes every 30 seconds</span><span>{loading ? "Syncing…" : "Last sync available from API"}</span></footer></div>{toast && <div className="toast" role="status">✓ {toast}</div>}</div>;
}

function AuditList({ items }: { items: Audit[] }) {
  if (!items.length) return <p className="muted-copy">No audit entries were returned for this case.</p>;
  return <div className="audit-list">{items.map((item) => <div className="audit-item" key={item.id}><span className="timeline-dot" /><div><div className="audit-item-head"><strong>{label(item.action)}</strong><time>{formatDate(item.event_time)}</time></div><p>{item.actor}{item.detail ? ` · ${Object.entries(item.detail).map(([key, value]) => `${label(key)}: ${String(value)}`).join(", ")}` : ""}</p></div></div>)}</div>;
}

function AuditPage({ items, selected }: { items: Audit[]; selected: Exception | null }) {
  return <><PageIntro eyebrow="Governance" title="Audit trail" copy="A chronological record of diagnosis, policy, guardrail, and execution decisions." />{selected ? <section className="panel audit-page"><div className="panel-heading"><div><p className="panel-kicker">Selected case</p><h2>{selected.razorpay_subscription_id}</h2></div><Status status={selected.outcome} /></div><AuditList items={items} /></section> : <section className="panel"><EmptyState title="Select an exception to inspect" copy="Open a case from the Exceptions queue to review its recorded decisions." /></section>}</>;
}

export default App;
