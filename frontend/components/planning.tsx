"use client";

import { useState } from "react";
import {
  ArrowDownLeft,
  ArrowUpRight,
  CalendarDays,
  ChevronRight,
  Copy,
  Download,
  Plus,
  Sprout,
  Trash2,
  TrendingUp,
} from "lucide-react";
import {
  api,
  Entity,
  errorMessage,
  decimalSum,
  decimalSubtract,
  percentageToRate,
  money,
  monthNow,
  prettyDate,
  query,
  today,
} from "@/lib/api";
import {
  AddButton,
  Button,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Modal,
  PageHead,
  Panel,
  SpendingBars,
  Stat,
  TrendChart,
  useApp,
  useLoad,
} from "./ui";
import { RecordEditor, TransactionEditor } from "./editors";
import {
  DeleteDialog,
  ResourceTable,
  TransactionTable,
  useFields,
} from "./records";

export function Dashboard() {
  const { version, accounts, navigate } = useApp();
  const [month, setMonth] = useState(monthNow());
  const [editor, setEditor] = useState<Entity | null | undefined>();
  const start = `${month}-01`,
    end = new Date(Number(month.slice(0, 4)), Number(month.slice(5)), 0)
      .toISOString()
      .slice(0, 10);
  const { data, loading, error } = useLoad<any>(
    `/dashboard?start=${start}&end=${end}`,
    null,
    version,
  );
  const transactions = useLoad<any>(
    "/transactions?page_size=5",
    { items: [] },
    version,
  );
  return (
    <>
      <PageHead
        eyebrow="A LITTLE CLARITY, EVERY DAY"
        title="The bigger picture"
        subtitle="A quiet place to keep your household finances in order."
        action={
          <>
            <label className="period-picker">
              <CalendarDays size={16} />
              <input
                type="month"
                value={month}
                onChange={(e) => setMonth(e.target.value)}
              />
            </label>
            <AddButton onClick={() => setEditor(null)}>
              Add transaction
            </AddButton>
          </>
        }
      />
      <ErrorBox error={error} />
      {loading ? (
        <Loading />
      ) : (
        data && (
          <>
            {data.warnings?.map((w: string) => (
              <div className="notice" key={w}>
                Incomplete totals: {w}
              </div>
            ))}
            <div className="stats four">
              <Stat
                label="Household net worth"
                value={money(data.net_worth)}
                hint={`${accounts.filter((a: Entity) => ["asset", "liability"].includes(a.account_class)).length} financial accounts · valued in USD`}
                accent
                icon={<Sprout size={21} />}
              />
              <Stat
                label="Income this period"
                value={money(data.income)}
                hint="Money coming in"
                icon={<ArrowDownLeft size={20} />}
              />
              <Stat
                label="Spent this period"
                value={money(data.expenses)}
                hint="Expenses, less refunds"
                icon={<ArrowUpRight size={20} />}
              />
              <Stat
                label="Investment returns"
                value={money(data.returns)}
                hint="Cash investment earnings"
                icon={<TrendingUp size={20} />}
              />
            </div>
            {!accounts.some((a: Entity) => a.account_class === "asset") && (
              <div className="welcome-banner">
                <div className="welcome-illustration">
                  <Sprout size={43} strokeWidth={1.2} />
                </div>
                <div>
                  <div className="eyebrow">LET’S PUT DOWN ROOTS</div>
                  <h2>A little setup. A lot more peace of mind.</h2>
                  <p>
                    Add your first account, set a starting balance, and make
                    room for what matters.
                  </p>
                </div>
                <Button onClick={() => navigate("accounts")}>
                  Add your first account <ChevronRight size={16} />
                </Button>
              </div>
            )}
            <div className="dashboard-grid">
              <Panel
                title="Money in, money out"
                subtitle="Your monthly cash flow"
                action={<span className="pill">USD</span>}
              >
                <TrendChart items={data.monthly_trend || []} />
              </Panel>
              <Panel
                title="Where it’s going"
                subtitle="Spending by category"
                action={
                  <button
                    className="text-link"
                    onClick={() => navigate("budgets")}
                  >
                    View budgets <ChevronRight size={14} />
                  </button>
                }
              >
                <SpendingBars items={data.category_spending || []} />
              </Panel>
            </div>
            <div className="dashboard-grid bottom">
              <Panel
                title="Recent transactions"
                subtitle="The latest entries in your ledger"
                action={
                  <button
                    className="text-link"
                    onClick={() => navigate("transactions")}
                  >
                    View all <ChevronRight size={14} />
                  </button>
                }
              >
                {transactions.data.items?.length ? (
                  <TransactionTable
                    items={transactions.data.items}
                    onEdit={setEditor}
                  />
                ) : (
                  <Empty
                    title="Your story starts with a transaction"
                    message="Add your first entry and we’ll take care of the totals."
                    action={
                      <Button
                        variant="secondary"
                        onClick={() => setEditor(null)}
                      >
                        <Plus size={16} />
                        Record a transaction
                      </Button>
                    }
                  />
                )}
              </Panel>
              <Panel
                title="What you hold"
                subtitle="Assets and liabilities at a glance"
              >
                <div className="holdings-summary">
                  <div>
                    <span>
                      <i className="green-dot" />
                      Assets
                    </span>
                    <strong>{money(data.assets)}</strong>
                  </div>
                  <div>
                    <span>
                      <i className="sand-dot" />
                      Liabilities
                    </span>
                    <strong>{money(data.liabilities)}</strong>
                  </div>
                  <div className="holdings-line">
                    <span>Net worth</span>
                    <strong>{money(data.net_worth)}</strong>
                  </div>
                </div>
                <div className="gentle-note">
                  <Sprout size={19} />
                  <p>
                    Small habits. Strong foundations.
                    <br />
                    <span>Keep your records current, one entry at a time.</span>
                  </p>
                </div>
                <button
                  className="panel-bottom-link"
                  onClick={() => navigate("accounts")}
                >
                  Explore your accounts <ArrowUpRight size={16} />
                </button>
              </Panel>
            </div>
          </>
        )
      )}
      {editor !== undefined && (
        <TransactionEditor
          record={editor || undefined}
          onClose={() => setEditor(undefined)}
        />
      )}
    </>
  );
}

export function Budgets() {
  const { version, notify, refresh } = useApp();
  const [month, setMonth] = useState(monthNow());
  const [editor, setEditor] = useState<Entity | null | undefined>();
  const [copy, setCopy] = useState(false);
  const [source, setSource] = useState(() => {
    const d = new Date();
    d.setDate(1);
    d.setMonth(d.getMonth() - 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [removed, setRemoved] = useState<Entity | null>(null);
  const [categoryPreset, setCategoryPreset] = useState("");
  const fields = useFields("budgets").map((f) =>
    f.name === "month"
      ? { ...f, default: month }
      : f.name === "category_id"
        ? { ...f, default: categoryPreset }
        : f,
  );
  const summary = useLoad<any>(
    `/budget-summary?month=${month}`,
    { items: [], total_budget: "0", total_actual: "0" },
    version,
  );
  const records = useLoad<any>(
    `/budgets?month=${month}&page_size=100`,
    { items: [] },
    version,
  );
  async function copyBudget(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/budgets/copy", {
        method: "POST",
        body: JSON.stringify({ source_month: source, target_month: month }),
      });
      notify("Budget copied.");
      refresh();
      setCopy(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHead
        title="Make room for what matters"
        subtitle="A monthly plan that grows with your household."
        action={
          <>
            <input
              aria-label="Budget month"
              type="month"
              value={month}
              onChange={(e) => setMonth(e.target.value)}
            />
            <Button variant="secondary" onClick={() => setCopy(true)}>
              <Copy size={15} />
              Copy month
            </Button>
            <AddButton onClick={() => setEditor(null)}>Set a budget</AddButton>
          </>
        }
      />
      <div className="stats three">
        <Stat
          label="Monthly plan"
          value={money(summary.data.total_budget)}
          accent
        />
        <Stat label="Spent so far" value={money(summary.data.total_actual)} />
        <Stat
          label="Remaining"
          value={money(
            decimalSubtract(
              summary.data.total_budget,
              summary.data.total_actual,
            ),
          )}
          hint="Plan minus actual expenses"
        />
      </div>
      <Panel
        title="Category budgets"
        subtitle="Transfers are excluded. Refunds reduce spending on their recorded date."
      >
        <ErrorBox error={summary.error} />
        {summary.loading ? (
          <Loading />
        ) : summary.data.items.length ? (
          <div className="budget-list">
            {summary.data.items.map((item: any) => {
              const record = records.data.items.find(
                (r: Entity) => r.category_id === item.category_id,
              );
              const percent =
                Number(item.budget) > 0
                  ? (Number(item.actual) / Number(item.budget)) * 100
                  : 0;
              return (
                <div className="budget-row" key={item.category_id || "none"}>
                  <div className="budget-main">
                    <div className="budget-title">
                      <strong>{item.name || "Uncategorized"}</strong>
                      <span
                        className={Number(item.remaining) < 0 ? "negative" : ""}
                      >
                        {money(item.remaining)}{" "}
                        {Number(item.remaining) < 0 ? "over" : "left"}
                      </span>
                    </div>
                    <div className="bar-track">
                      <span
                        className={percent > 100 ? "over" : ""}
                        style={{
                          width: `${Math.min(Math.max(percent, 0), 100)}%`,
                        }}
                      />
                    </div>
                    <div className="budget-caption">
                      <span>{money(item.actual)} spent</span>
                      <span>of {money(item.budget)} planned</span>
                    </div>
                  </div>
                  <div className="budget-actions">
                    <Button
                      variant="text"
                      onClick={() => {
                        setCategoryPreset(item.category_id || "");
                        setEditor(record || null);
                      }}
                    >
                      {record ? "Adjust" : "Set target"}
                    </Button>
                    {record && (
                      <button
                        className="icon-button subtle-danger"
                        title="Remove target"
                        onClick={() => setRemoved(record)}
                      >
                        <Trash2 size={15} />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <Empty
            title="Give your money a direction"
            message="Choose a category and a monthly target to start your plan."
            action={
              <AddButton onClick={() => setEditor(null)}>
                Create your first budget
              </AddButton>
            }
          />
        )}
      </Panel>
      {editor !== undefined && (
        <RecordEditor
          resource="budgets"
          title="Budget"
          fields={fields}
          record={editor || undefined}
          onClose={() => setEditor(undefined)}
        />
      )}{" "}
      {removed && (
        <DeleteDialog
          resource="budgets"
          record={removed}
          onClose={() => setRemoved(null)}
        />
      )}{" "}
      {copy && (
        <Modal
          title="Copy a monthly budget"
          subtitle={`Copy category targets into ${month}.`}
          onClose={() => setCopy(false)}
        >
          <form onSubmit={copyBudget}>
            <div className="form-grid">
              <Field label="Copy from month">
                <input
                  required
                  type="month"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                />
              </Field>
            </div>
            <ErrorBox error={error} />
            <div className="modal-footer">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setCopy(false)}
              >
                Cancel
              </Button>
              <Button busy={busy}>Copy targets</Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}

export function Subscriptions() {
  return (
    <ResourceTable
      resource="subscriptions"
      title="Subscriptions"
      description="Know what’s coming, before it arrives. Expected payments stay separate from your ledger."
      columns={[
        {
          key: "name",
          label: "Subscription",
          render: (r) => <strong>{r.name}</strong>,
        },
        {
          key: "amount",
          label: "Expected amount",
          render: (r) => money(r.amount, r.currency),
        },
        {
          key: "frequency",
          label: "Repeats",
          render: (r) => `Every ${r.interval || 1} ${r.frequency}`,
        },
        {
          key: "start_date",
          label: "Starts",
          render: (r) => prettyDate(r.start_date),
        },
        {
          key: "end_date",
          label: "Ends",
          render: (r) => (r.end_date ? prettyDate(r.end_date) : "Ongoing"),
        },
        { key: "type", label: "Type" },
      ]}
    />
  );
}

export function Forecasts() {
  const { version } = useApp();
  const [horizon, setHorizon] = useState("month");
  const { data, loading, error } = useLoad<any>(
    `/forecast?horizon=${horizon}`,
    null,
    version,
  );
  return (
    <>
      <PageHead
        eyebrow="LOOKING AHEAD"
        title="A little foresight"
        subtitle="Understand tomorrow’s expenses with what you know today."
        action={
          <div className="segmented compact">
            {["month", "quarter", "year"].map((h) => (
              <button
                className={horizon === h ? "active" : ""}
                key={h}
                onClick={() => setHorizon(h)}
              >
                Next {h}
              </button>
            ))}
          </div>
        }
      />
      <ErrorBox error={error} />
      {loading ? (
        <Loading />
      ) : (
        data && (
          <>
            {data.warnings?.map((w: string) => (
              <div className="notice" key={w}>
                Incomplete forecast: {w}
              </div>
            ))}
            <div className="forecast-banner">
              <div>
                <span className="eyebrow">
                  EXPECTED EXPENSES · {prettyDate(data.start)} –{" "}
                  {prettyDate(data.end)}
                </span>
                <div className="forecast-total">{money(data.total)}</div>
                <p>
                  A guide for planning, built from recurring payments and your
                  recent habits.
                </p>
              </div>
              <div className="forecast-emblem">
                <Sprout size={82} strokeWidth={0.8} />
              </div>
            </div>
            <div className="stats three">
              <Stat
                label="Scheduled subscriptions"
                value={money(data.recurring)}
                hint="Unfulfilled expense occurrences"
              />
              <Stat
                label="Everyday spending estimate"
                value={money(data.nonrecurring)}
                hint="Average nonrecurring category expenses"
              />
              <Stat
                label="Historical coverage"
                value={`${data.history_months || 0} months`}
                hint={
                  data.history_months
                    ? "Complete months used in the estimate"
                    : "No complete months yet; subscriptions only"
                }
              />
            </div>
            <div className="dashboard-grid">
              <Panel
                title="Expected by category"
                subtitle="Scheduled and estimated expenses combined"
              >
                <SpendingBars items={data.items || []} />
              </Panel>
              <Panel title="How this is calculated">
                <div className="explanation">
                  <span className="step-number">01</span>
                  <div>
                    <h3>Start with what’s scheduled</h3>
                    <p>
                      Upcoming expense subscriptions, excluding occurrences
                      you’ve already paid.
                    </p>
                  </div>
                  <span className="step-number">02</span>
                  <div>
                    <h3>Add your everyday habits</h3>
                    <p>
                      The average from up to three complete months of
                      nonrecurring expenses, extended over this period.
                    </p>
                  </div>
                  <span className="step-number">03</span>
                  <div>
                    <h3>Keep a little perspective</h3>
                    <p>
                      These are estimates. Budgets are comparison targets, and
                      transfers never count as spending.
                    </p>
                  </div>
                </div>
              </Panel>
            </div>
            <Panel
              title="Upcoming occurrences"
              subtitle="Expected transactions — no money has been posted yet"
            >
              {data.occurrences?.length ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Subscription</th>
                        <th>Due</th>
                        <th className="number">Expected (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.occurrences.map((o: any, i: number) => (
                        <tr key={i}>
                          <td>{o.name || o.subscription_name}</td>
                          <td>{prettyDate(o.date)}</td>
                          <td className="number">
                            {money(o.usd_amount ?? o.amount)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty
                  title="Nothing scheduled for this period"
                  message="Add recurring expenses in Subscriptions to build out this forecast."
                />
              )}
            </Panel>
          </>
        )
      )}
    </>
  );
}

export function Investments() {
  const { accounts, version, notify, refresh } = useApp();
  const [tab, setTab] = useState("projections");
  const owned = accounts.filter((a: Entity) =>
    ["asset", "liability"].includes(a.account_class),
  );
  const [date, setDate] = useState(`${new Date().getFullYear() + 5}-12-31`);
  const [rates, setRates] = useState<Record<string, string>>({});
  const [modes, setModes] = useState<Record<string, string>>({});
  const [flows, setFlows] = useState<any[]>([]);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [performanceId, setPerformanceId] = useState("");
  const [scenarioName, setScenarioName] = useState("");
  const [selectedScenario, setSelectedScenario] = useState<Entity | null>(null);
  const [removedScenario, setRemovedScenario] = useState<Entity | null>(null);
  const [saving, setSaving] = useState(false);
  const scenarios = useLoad<any>(
    "/scenarios?page_size=100",
    { items: [] },
    version,
  );
  const payload = () => ({
    date,
    assumptions: owned.map((a: Entity) => ({
      account_id: a.id,
      mode: modes[a.id] || "manual",
      annual_rate: percentageToRate(rates[a.id] || "0"),
    })),
    flows,
  });
  async function calculate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      setResult(
        await api("/projections", {
          method: "POST",
          body: JSON.stringify(payload()),
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
      notify(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  }
  async function saveScenario() {
    setSaving(true);
    try {
      const saved = await api(
        `/scenarios${selectedScenario ? `/${selectedScenario.id}` : ""}`,
        {
          method: selectedScenario ? "PATCH" : "POST",
          body: JSON.stringify({
            name: scenarioName,
            ...payload(),
            revision: selectedScenario?.revision,
          }),
        },
      );
      notify("Scenario saved.");
      setSelectedScenario(saved);
      refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }
  function loadScenario(id: string) {
    if (!id) {
      setSelectedScenario(null);
      setScenarioName("");
      return;
    }
    const s = scenarios.data.items.find((v: Entity) => v.id === id);
    if (!s) return;
    setSelectedScenario(s);
    setScenarioName(s.name);
    setDate(s.date);
    setRates(
      Object.fromEntries(
        (s.assumptions || []).map((a: any) => [
          a.account_id,
          String(Number(a.annual_rate) * 100),
        ]),
      ),
    );
    setModes(
      Object.fromEntries(
        (s.assumptions || []).map((a: any) => [a.account_id, a.mode]),
      ),
    );
    setFlows(s.flows || []);
    setResult(null);
  }
  return (
    <>
      <PageHead
        eyebrow="THE LONG VIEW"
        title="Let your future take shape"
        subtitle="Track market values and explore the possibilities ahead."
      />
      <div className="tabs page-tabs">
        {[
          ["projections", "Net worth projection"],
          ["valuations", "Account valuations"],
          ["performance", "Historical performance"],
        ].map(([v, l]) => (
          <button
            key={v}
            className={tab === v ? "active" : ""}
            onClick={() => setTab(v)}
          >
            {l}
          </button>
        ))}
      </div>
      {tab === "valuations" ? (
        <ResourceTable
          resource="valuations"
          title="Valuations"
          heading={false}
          description="Record an account’s full end-of-day market value, including cash."
          columns={[
            {
              key: "account_id",
              label: "Account",
              render: (r) =>
                accounts.find((a: Entity) => a.id === r.account_id)?.name ||
                r.account_id,
            },
            { key: "date", label: "Date", render: (r) => prettyDate(r.date) },
            {
              key: "amount",
              label: "Market value",
              render: (r) =>
                money(
                  r.amount,
                  accounts.find((a: Entity) => a.id === r.account_id)?.currency,
                ),
            },
          ]}
        />
      ) : tab === "performance" ? (
        <>
          <Panel
            title="Historical investment performance"
            subtitle="A Modified Dietz estimate using dated valuations and external cash flows."
          >
            <div className="panel-body">
              <Field label="Account">
                <select
                  value={performanceId}
                  onChange={(e) => setPerformanceId(e.target.value)}
                >
                  <option value="">Select an account…</option>
                  {owned.map((a: Entity) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </Panel>
          {performanceId && <Performance accountId={performanceId} />}
        </>
      ) : (
        <>
          <form onSubmit={calculate}>
            <Panel
              title="Your assumptions"
              subtitle="Annual rates are total USD returns, including dividends and interest."
              action={
                scenarios.data.items.length ? (
                  <select
                    aria-label="Load scenario"
                    value={selectedScenario?.id || ""}
                    onChange={(e) => loadScenario(e.target.value)}
                  >
                    <option value="">New scenario…</option>
                    {scenarios.data.items.map((s: Entity) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                ) : undefined
              }
            >
              <div className="panel-body">
                <div className="projection-date">
                  <Field label="Project through">
                    <input
                      type="date"
                      required
                      min={today()}
                      value={date}
                      onChange={(e) => setDate(e.target.value)}
                    />
                  </Field>
                  <p className="muted">
                    Choose a horizon, then a return assumption for each account.
                    A 0% default keeps the starting value constant.
                  </p>
                </div>
                {owned.length ? (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Account</th>
                          <th>Return assumption</th>
                          <th>Annual return (%)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {owned.map((a: Entity) => (
                          <tr key={a.id}>
                            <td>
                              <strong>{a.name}</strong>
                              <div className="cell-sub">
                                {a.type} · {money(a.balance, a.currency)}
                              </div>
                            </td>
                            <td>
                              <select
                                aria-label={`${a.name} return mode`}
                                value={modes[a.id] || "manual"}
                                onChange={(e) =>
                                  setModes({ ...modes, [a.id]: e.target.value })
                                }
                              >
                                <option value="manual">Expected return</option>
                                <option value="historical">
                                  Historical estimate
                                </option>
                              </select>
                            </td>
                            <td>
                              <input
                                className="rate-input"
                                aria-label={`${a.name} annual return`}
                                type="number"
                                min="-99.99"
                                step="0.01"
                                disabled={modes[a.id] === "historical"}
                                value={rates[a.id] || "0"}
                                onChange={(e) =>
                                  setRates({ ...rates, [a.id]: e.target.value })
                                }
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <Empty
                    title="Add an account to start projecting"
                    message="Your financial accounts become the starting point for the projection."
                  />
                )}
                <h3 className="form-section-title">
                  Planned contributions & withdrawals
                </h3>
                <p className="muted small">
                  Positive amounts add money; negative amounts withdraw it.
                  Amounts are USD. Represent both sides of transfers between
                  projected accounts.
                </p>
                {flows.map((f, i) => (
                  <div className="flow-row" key={i}>
                    <select
                      aria-label="Flow account"
                      required
                      value={f.account_id}
                      onChange={(e) =>
                        setFlows(
                          flows.map((v, j) =>
                            i === j ? { ...v, account_id: e.target.value } : v,
                          ),
                        )
                      }
                    >
                      <option value="">Select account…</option>
                      {owned.map((a: Entity) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label="Flow date"
                      required
                      type="date"
                      min={today()}
                      max={date}
                      value={f.date}
                      onChange={(e) =>
                        setFlows(
                          flows.map((v, j) =>
                            i === j ? { ...v, date: e.target.value } : v,
                          ),
                        )
                      }
                    />
                    <input
                      aria-label="Flow amount in USD"
                      required
                      inputMode="decimal"
                      placeholder="USD amount"
                      value={f.amount}
                      onChange={(e) =>
                        setFlows(
                          flows.map((v, j) =>
                            i === j ? { ...v, amount: e.target.value } : v,
                          ),
                        )
                      }
                    />
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Remove flow"
                      onClick={() => setFlows(flows.filter((_, j) => i !== j))}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
                <Button
                  variant="text"
                  type="button"
                  onClick={() =>
                    setFlows([
                      ...flows,
                      {
                        account_id: "",
                        date: new Date(Date.now() + 86400000)
                          .toISOString()
                          .slice(0, 10),
                        amount: "",
                      },
                    ])
                  }
                >
                  <Plus size={15} />
                  Add planned movement
                </Button>
                <ErrorBox error={error} />
                <div className="form-bottom">
                  <Button busy={busy} disabled={!owned.length}>
                    <TrendingUp size={17} />
                    Calculate projection
                  </Button>
                </div>
              </div>
            </Panel>
          </form>
          {result && (
            <>
              <div className="stats three">
                <Stat
                  label={`Projected net worth · ${prettyDate(result.date)}`}
                  value={money(result.total)}
                  accent
                />
                <Stat
                  label="Accounts included"
                  value={result.accounts?.length || 0}
                />
                <Stat
                  label="Method"
                  value={<span className="small-stat">Compound growth</span>}
                  hint="With dated contributions & withdrawals"
                />
              </div>
              {result.warnings?.map((w: string) => (
                <div className="notice" key={w}>
                  {w}
                </div>
              ))}
              <Panel
                title="The path ahead"
                subtitle="An illustration based on your assumptions, not a prediction."
              >
                <TrendChart items={result.series || []} projection />
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Account</th>
                        <th className="number">Current USD</th>
                        <th className="number">Projected USD</th>
                        <th className="number">Annual return</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.accounts?.map((a: any) => (
                        <tr key={a.account_id}>
                          <td>{a.name}</td>
                          <td className="number">{money(a.current_value)}</td>
                          <td className="number">{money(a.projected_value)}</td>
                          <td className="number">
                            {(Number(a.annual_rate) * 100).toFixed(2)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="save-scenario">
                  {selectedScenario && (
                    <Button
                      variant="text"
                      onClick={() => setRemovedScenario(selectedScenario)}
                    >
                      <Trash2 size={15} />
                      Remove scenario
                    </Button>
                  )}
                  <input
                    aria-label="Scenario name"
                    placeholder="Name this scenario…"
                    value={scenarioName}
                    onChange={(e) => setScenarioName(e.target.value)}
                  />
                  <Button
                    variant="secondary"
                    busy={saving}
                    disabled={!scenarioName}
                    onClick={saveScenario}
                  >
                    {selectedScenario ? "Update scenario" : "Save scenario"}
                  </Button>
                </div>
              </Panel>
            </>
          )}
        </>
      )}
      {removedScenario && (
        <DeleteDialog
          resource="scenarios"
          record={removedScenario}
          onClose={() => setRemovedScenario(null)}
          onSaved={() => {
            setSelectedScenario(null);
            setScenarioName("");
          }}
        />
      )}
    </>
  );
}
function Performance({ accountId }: { accountId: string }) {
  const { version } = useApp();
  const { data, loading, error } = useLoad<any>(
    `/performance?account_id=${accountId}`,
    null,
    version,
  );
  return (
    <Panel title="Estimated returns">
      <ErrorBox error={error} />
      {loading ? (
        <Loading />
      ) : data?.available ? (
        <>
          <div className="stats three inset">
            <Stat
              label="Cumulative return"
              value={`${(Number(data.cumulative_return) * 100).toFixed(2)}%`}
              accent
            />
            <Stat
              label="Annualized return"
              value={
                data.annualized_return == null
                  ? "Insufficient history"
                  : `${(Number(data.annualized_return) * 100).toFixed(2)}%`
              }
            />
            <Stat
              label="Valuation coverage"
              value={`${data.days} days`}
              hint={`${prettyDate(data.start)} – ${prettyDate(data.end)}`}
            />
          </div>
          <p className="panel-body muted">
            Estimated using Modified Dietz cash-flow weighting. Historical
            projection mode requires at least one year of valid coverage.
          </p>
        </>
      ) : (
        <Empty
          title="A little more history is needed"
          message={
            data?.reason ||
            "Record consecutive dated valuations to estimate performance."
          }
        />
      )}
    </Panel>
  );
}

export function Reports() {
  const { accounts, categories, tags, subscriptions, meta, version } = useApp();
  const [period, setPeriod] = useState("month");
  const [csvSection, setCsvSection] = useState("transactions");
  const [filters, setFilters] = useState<Record<string, string>>({
    start: `${monthNow()}-01`,
    end: today(),
    account_id: "",
    account_type: "",
    type: "",
    category_id: "",
    tag_id: "",
    subscription_id: "",
    currency: "",
  });
  const [applied, setApplied] = useState(filters);
  const [editor, setEditor] = useState<Entity | null>(null);
  const report = useLoad<any>(`/reports/data?${query(applied)}`, null, version);
  const change = (k: string, v: string) => setFilters({ ...filters, [k]: v });
  function changePeriod(value: string) {
    setPeriod(value);
    if (value === "custom") return;
    const d = new Date();
    const year = d.getFullYear(),
      month = d.getMonth();
    const startMonth =
      value === "year"
        ? 0
        : value === "quarter"
          ? Math.floor(month / 3) * 3
          : month;
    const endMonth =
      value === "year" ? 11 : value === "quarter" ? startMonth + 2 : month;
    setFilters({
      ...filters,
      start: `${year}-${String(startMonth + 1).padStart(2, "0")}-01`,
      end: new Date(year, endMonth + 1, 0).toISOString().slice(0, 10),
    });
  }
  return (
    <>
      <PageHead
        title="Your finances, on the record"
        subtitle="Summaries, trends, and a clear trail of every change."
        action={
          <>
            <select
              aria-label="CSV report section"
              value={csvSection}
              onChange={(event) => setCsvSection(event.target.value)}
            >
              <option value="transactions">Transactions</option>
              <option value="summary">Summary</option>
              <option value="cash-flow">Cash flow</option>
              <option value="budgets">Budget comparison</option>
              <option value="balances">Account balances</option>
              <option value="net-worth">Net worth trend</option>
              <option value="audit">Audit trail</option>
            </select>
            <a
              className="button secondary"
              href={`/api/v1/reports/csv?${query({ ...applied, section: csvSection })}`}
            >
              <Download size={16} />
              CSV
            </a>
            <a
              className="button primary"
              href={`/api/v1/reports/pdf?${query(applied)}`}
            >
              <Download size={16} />
              PDF report
            </a>
          </>
        }
      />
      {report.data?.balance_summary?.incomplete && (
        <div className="notice" role="status">
          <strong>Balance totals are incomplete.</strong>{" "}
          {(report.data.balance_summary.warnings || []).join(" ")} Add the
          missing exchange rates to include every account.
        </div>
      )}
      <Panel title="Report filters">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setApplied({ ...filters });
          }}
        >
          <div className="filter-grid report-filters">
            <Field label="Period">
              <select
                value={period}
                onChange={(e) => changePeriod(e.target.value)}
              >
                {[
                  ["month", "This month"],
                  ["quarter", "This quarter"],
                  ["year", "This year"],
                  ["custom", "Custom dates"],
                ].map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="From">
              <input
                type="date"
                required
                value={filters.start}
                onChange={(e) => {
                  change("start", e.target.value);
                  setPeriod("custom");
                }}
              />
            </Field>
            <Field label="Through">
              <input
                type="date"
                required
                value={filters.end}
                onChange={(e) => {
                  change("end", e.target.value);
                  setPeriod("custom");
                }}
              />
            </Field>
            <Field label="Account">
              <select
                value={filters.account_id}
                onChange={(e) => change("account_id", e.target.value)}
              >
                <option value="">All accounts</option>
                {accounts.map((a: Entity) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Account type">
              <select
                value={filters.account_type}
                onChange={(e) => change("account_type", e.target.value)}
              >
                <option value="">All account types</option>
                {meta?.account_types?.map((t: string) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </Field>
            <Field label="Transaction type">
              <select
                value={filters.type}
                onChange={(e) => change("type", e.target.value)}
              >
                <option value="">All types</option>
                {["Income", "Expense", "Transfer", "Return"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </Field>
            <Field label="Category">
              <select
                value={filters.category_id}
                onChange={(e) => change("category_id", e.target.value)}
              >
                <option value="">All categories</option>
                {categories.map((c: Entity) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Tag">
              <select
                value={filters.tag_id}
                onChange={(e) => change("tag_id", e.target.value)}
              >
                <option value="">All tags</option>
                {tags.map((c: Entity) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Subscription">
              <select
                value={filters.subscription_id}
                onChange={(e) => change("subscription_id", e.target.value)}
              >
                <option value="">All subscriptions</option>
                {subscriptions.map((c: Entity) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Currency">
              <input
                value={filters.currency}
                onChange={(e) =>
                  change("currency", e.target.value.toUpperCase())
                }
                placeholder="Any currency"
              />
            </Field>
            <div className="filter-submit">
              <Button>Apply filters</Button>
            </div>
          </div>
        </form>
      </Panel>
      <ErrorBox error={report.error} />
      {report.loading ? (
        <Loading />
      ) : (
        report.data && (
          <>
            <div className="stats four">
              <Stat
                label="Income"
                value={money(report.data.summary?.income)}
                accent
              />
              <Stat
                label="Expenses"
                value={money(report.data.summary?.expenses)}
              />
              <Stat
                label="Returns"
                value={money(report.data.summary?.returns)}
              />
              <Stat
                label="Net cash flow"
                value={money(report.data.summary?.net)}
              />
            </div>
            <div className="dashboard-grid">
              <Panel title="Income & expenses">
                <TrendChart items={report.data.monthly_trend || []} />
              </Panel>
              <Panel title="Spending by category">
                <SpendingBars items={report.data.category_spending || []} />
              </Panel>
            </div>
            <Panel
              title="Transaction detail"
              subtitle={`${report.data.transactions?.length || 0} matching records`}
            >
              {report.data.transactions?.length ? (
                <TransactionTable
                  items={report.data.transactions}
                  onEdit={setEditor}
                />
              ) : (
                <Empty
                  title="No matching transactions"
                  message="Try a different period or adjust your filters."
                />
              )}
            </Panel>
          </>
        )
      )}
      {editor && (
        <TransactionEditor record={editor} onClose={() => setEditor(null)} />
      )}
    </>
  );
}
