"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ArrowRight,
  ArrowRightLeft,
  CalendarDays,
  ChartNoAxesCombined,
  Check,
  CircleHelp,
  FileChartColumn,
  House,
  Landmark,
  Leaf,
  LoaderCircle,
  LogOut,
  Menu,
  Settings2,
  ShieldCheck,
  Sprout,
  Target,
  TrendingUp,
  X,
  AlertCircle,
} from "lucide-react";
import {
  api,
  Entity,
  errorMessage,
  ListResult,
  Session,
  setCsrf,
  setCurrencyPrecisions,
} from "@/lib/api";
import { AppContext, Button, ErrorBox, Field, Loading, Modal } from "./ui";
import { Accounts, Transactions } from "./records";
import {
  Budgets,
  Dashboard,
  Forecasts,
  Investments,
  Reports,
  Subscriptions,
} from "./planning";
import { Settings } from "./settings";
import { DragonCaveArt, HoardMark } from "./hoard-art";

const nav = [
  { id: "dashboard", label: "Overview", icon: House },
  { id: "accounts", label: "Accounts", icon: Landmark },
  { id: "transactions", label: "Transactions", icon: ArrowRightLeft },
  { id: "budgets", label: "Budgets", icon: Target },
  { id: "subscriptions", label: "Subscriptions", icon: CalendarDays },
  { id: "forecasts", label: "Forecasts", icon: ChartNoAxesCombined },
  { id: "investments", label: "Investments", icon: TrendingUp },
  { id: "reports", label: "Reports", icon: FileChartColumn },
];
async function allRecords(resource: string) {
  let page = 1,
    result: Entity[] = [];
  while (true) {
    const response = await api<ListResult>(
      `/${resource}?page=${page}&page_size=100`,
    );
    result = [...result, ...response.items];
    if (!response.items.length || result.length >= response.total) break;
    page++;
    if (page > 1000) throw new Error("Too many records to load.");
  }
  return result;
}

export default function Workspace() {
  const [session, setSession] = useState<Session | null>(null);
  const [sessionError, setSessionError] = useState("");
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState("dashboard");
  const [mobile, setMobile] = useState(false);
  const [logoutOpen, setLogoutOpen] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const closeLogout = useCallback(() => {
    if (!logoutBusy) setLogoutOpen(false);
  }, [logoutBusy]);
  const [version, setVersion] = useState(0);
  const [accounts, setAccounts] = useState<Entity[]>([]);
  const [categories, setCategories] = useState<Entity[]>([]);
  const [tags, setTags] = useState<Entity[]>([]);
  const [subscriptions, setSubscriptions] = useState<Entity[]>([]);
  const [meta, setMeta] = useState<any>(null);
  const [hoardName, setHoardName] = useState("Our hoard");
  const [referenceError, setReferenceError] = useState("");
  const [toasts, setToasts] = useState<
    { id: number; message: string; type: string }[]
  >([]);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const notify = useCallback((message: string, type = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, type }]);
    setTimeout(() => setToasts((t) => t.filter((v) => v.id !== id)), 6500);
  }, []);
  const navigate = useCallback((id: string) => {
    setView(id);
    setMobile(false);
    window.location.hash = id;
    window.scrollTo({ top: 0, behavior: "instant" });
  }, []);
  const loadSession = useCallback(async () => {
    setLoading(true);
    setSessionError("");
    try {
      const s = await api<Session>("/auth/session");
      setSession(s);
      setCsrf(s.csrf_token);
    } catch (e) {
      setSessionError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    loadSession();
    const readHash = () => {
      const id = window.location.hash.slice(1);
      if ([...nav.map((n) => n.id), "settings"].includes(id)) setView(id);
    };
    readHash();
    window.addEventListener("hashchange", readHash);
    return () => window.removeEventListener("hashchange", readHash);
  }, [loadSession]);
  useEffect(() => {
    if (!session?.user) return;
    let active = true;
    setReferenceError("");
    Promise.all([
      allRecords("accounts"),
      allRecords("categories"),
      allRecords("tags"),
      allRecords("subscriptions"),
      api("/meta"),
      api("/settings"),
    ])
      .then(([a, c, t, s, m, preferences]) => {
        if (active) {
          setAccounts(a);
          setCategories(c);
          setTags(t);
          setSubscriptions(s);
          setCurrencyPrecisions(m.currencies || []);
          setMeta(m);
          setHoardName(preferences.household_name || "Our hoard");
        }
      })
      .catch((e) => {
        if (active) setReferenceError(errorMessage(e));
      });
    return () => {
      active = false;
    };
  }, [session?.user?.id, version]);
  async function logout() {
    if (logoutBusy) return;
    setLogoutBusy(true);
    setLogoutError("");
    try {
      await api("/auth/logout", { method: "POST" });
      setLogoutOpen(false);
      setMobile(false);
      setSession(null);
      setCsrf("");
      setAccounts([]);
      setCategories([]);
      setTags([]);
      setSubscriptions([]);
      await loadSession();
    } catch (e) {
      setLogoutError(errorMessage(e));
    } finally {
      setLogoutBusy(false);
    }
  }
  if (loading)
    return (
      <div className="startup">
        <Brand />
        <Loading />
      </div>
    );
  if (sessionError)
    return (
      <div className="startup">
        <Brand />
        <ErrorBox error={sessionError} />
        <Button onClick={loadSession}>Try again</Button>
      </div>
    );
  if (!session?.user)
    return <Auth session={session} onAuthenticated={loadSession} />;
  const pages: Record<string, React.ReactNode> = {
    dashboard: <Dashboard />,
    accounts: <Accounts />,
    transactions: <Transactions />,
    budgets: <Budgets />,
    subscriptions: <Subscriptions />,
    forecasts: <Forecasts />,
    investments: <Investments />,
    reports: <Reports />,
    settings: <Settings />,
  };
  return (
    <AppContext.Provider
      value={{
        session,
        accounts,
        categories,
        tags,
        subscriptions,
        meta,
        version,
        refresh,
        notify,
        navigate,
      }}
    >
      <div className="app-shell">
        {mobile && (
          <div className="mobile-scrim" onClick={() => setMobile(false)} />
        )}
        <aside className={`sidebar ${mobile ? "open" : ""}`}>
          <button
            className="brand-button"
            onClick={() => navigate("dashboard")}
          >
            <Brand />
          </button>
          <div className="hoard-label">
            <span className="hoard-avatar">
              <Leaf size={15} />
            </span>
            <div>
              {hoardName}
              <span>Your shared treasure ledger</span>
            </div>
          </div>
          <div className="nav-section-label">WORKSPACE</div>
          <nav aria-label="Main navigation">
            {nav.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                className={`nav-item ${view === id ? "active" : ""}`}
                onClick={() => navigate(id)}
                aria-current={view === id ? "page" : undefined}
              >
                <Icon size={18} strokeWidth={1.7} />
                {label}
                {view === id && <span className="active-dot" />}
              </button>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="private-note">
              <ShieldCheck size={17} />
              <div>
                Private by design<span>Your data stays at home.</span>
              </div>
            </div>
            <button
              className={`nav-item ${view === "settings" ? "active" : ""}`}
              onClick={() => navigate("settings")}
            >
              <Settings2 size={18} />
              Settings
            </button>
            <button
              className="nav-item"
              onClick={() => {
                setLogoutError("");
                setLogoutOpen(true);
              }}
            >
              <LogOut size={18} />
              Logout
            </button>
            <div className="user-row">
              <div className="user-avatar">
                {session.user.username[0].toUpperCase()}
              </div>
              <div>
                <strong>{session.user.username}</strong>
                <span>
                  {session.user.role === "admin"
                    ? "Administrator"
                    : "Hoard member"}
                </span>
              </div>
            </div>
          </div>
        </aside>
        <div className="main-column">
          <header className="topbar">
            <div className="topbar-left">
              <button
                className="icon-button mobile-menu"
                onClick={() => setMobile(!mobile)}
                aria-label="Open navigation"
              >
                <Menu size={21} />
              </button>
              <span>Workspace</span>
              <span className="breadcrumb-slash">/</span>
              <strong>
                {view === "settings"
                  ? "Settings"
                  : nav.find((n) => n.id === view)?.label}
              </strong>
            </div>
            <div className="topbar-right">
              <span className="live-status">
                <i />
                Local ledger
              </span>
              <span className="topbar-date">
                {new Date().toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                  year: "numeric",
                })}
              </span>
            </div>
          </header>
          <main id="main-content" className="main-content">
            {referenceError && (
              <ErrorBox
                error={`Could not load account references: ${referenceError}`}
              />
            )}{" "}
            {pages[view]}
          </main>
          <footer className="app-footer">
            <span>Hoard Counter</span>
            <span>Thoughtful finances. A steadier tomorrow.</span>
            <span>USD reporting</span>
          </footer>
        </div>
      </div>
      {logoutOpen && (
        <Modal title="Logout?" onClose={closeLogout}>
          <p className="dialog-copy">
            Are you sure you want to end your session?
          </p>
          <ErrorBox error={logoutError} />
          <div className="modal-footer">
            <Button
              variant="secondary"
              disabled={logoutBusy}
              onClick={closeLogout}
            >
              Cancel
            </Button>
            <Button busy={logoutBusy} onClick={logout}>
              Logout
            </Button>
          </div>
        </Modal>
      )}
      <div className="toast-stack" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`toast ${t.type}`}
            role={t.type === "error" ? "alert" : "status"}
          >
            {t.type === "error" ? (
              <AlertCircle size={18} />
            ) : (
              <Check size={18} />
            )}
            <span>{t.message}</span>
            <button
              className="icon-button"
              aria-label="Dismiss notification"
              onClick={() => setToasts(toasts.filter((v) => v.id !== t.id))}
            >
              <X size={15} />
            </button>
          </div>
        ))}
      </div>
    </AppContext.Provider>
  );
}

function Brand() {
  return (
    <div className="brand">
      <div className="brand-icon">
        <HoardMark />
      </div>
      <div>
        <strong>Hoard</strong>
        <span>COUNTING</span>
      </div>
    </div>
  );
}
function Auth({
  session,
  onAuthenticated,
}: {
  session: Session | null;
  onAuthenticated: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const setup = session?.setup_required;
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/auth/${setup ? "setup" : "login"}`, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      onAuthenticated();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-layout">
      <section className="auth-story">
        <Brand />
        <div className="auth-message">
          <div className="eyebrow">MAKE ROOM FOR WHAT MATTERS</div>
          <h1>
            A little clarity.
            <br />A steadier tomorrow.
          </h1>
          <p>
            Everything you earn, spend, save, and hope for.
            <br />
            Together in one thoughtful place.
          </p>
          <div className="dragon-cave-art" aria-hidden="true">
            <DragonCaveArt />
          </div>
        </div>
        <div className="auth-foot">
          <ShieldCheck size={16} />
          Private. Personal. Entirely yours.
        </div>
      </section>
      <section className="auth-form-area">
        <div className="auth-form">
          <div className="eyebrow">
            {setup ? "BUILD YOUR FIRST HOARD" : "WELCOME HOME"}
          </div>
          <h2>
            {setup ? "Start your hoard ledger" : "Good to see you again."}
          </h2>
          <p>
            {setup
              ? "Create an administrator account to bring your finances together."
              : "Sign in to count and care for your hoard."}
          </p>
          <form onSubmit={submit}>
            <Field label="Username">
              <input
                required
                autoComplete="username"
                minLength={setup ? 3 : 1}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Your username"
              />
            </Field>
            <Field
              label="Password"
              hint={
                setup
                  ? "Choose a password with at least 12 characters."
                  : undefined
              }
            >
              <input
                required
                type="password"
                autoComplete={setup ? "new-password" : "current-password"}
                minLength={setup ? 12 : 1}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={
                  setup ? "Create a strong password" : "Your password"
                }
              />
            </Field>
            <ErrorBox error={error} />
            <Button busy={busy} className="auth-submit">
              {setup ? "Create your hoard" : "Sign in"}
              <ArrowRight size={17} />
            </Button>
          </form>
          <p className="auth-small">
            Your records are stored privately on your hoard’s server.
          </p>
        </div>
      </section>
    </div>
  );
}
