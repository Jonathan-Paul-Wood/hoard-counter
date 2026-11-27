"use client";

import { HoardEggIcon } from "./hoard-art";

import { useState, useEffect, useRef } from "react";
import {
  ArrowDownLeft,
  ArrowUpRight,
  CalendarDays,
  ChevronRight,
  Download,
  Plus,
  Trash2,
  TrendingUp,
} from "lucide-react";
import {
  api,
  Entity,
  errorMessage,
  decimalSum,
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
        subtitle="A quiet place to keep your hoard finances in order."
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
                label="Hoard net worth"
                value={money(data.net_worth)}
                hint={`${accounts.filter((a: Entity) => ["asset", "liability"].includes(a.account_class)).length} financial accounts · valued in USD`}
                accent
                icon={<HoardEggIcon size={21} />}
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
                label="Net cash flow"
                value={money(data.net)}
                hint="Income less expenses"
                icon={<TrendingUp size={20} />}
              />
            </div>
            {!accounts.some((a: Entity) => a.account_class === "asset") && (
              <div className="welcome-banner">
                <div className="welcome-illustration">
                  <HoardEggIcon size={43} strokeWidth={1.2} />
                </div>
                <div>
                  <div className="eyebrow">LET’S GROW THE HOARD</div>
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
                  <HoardEggIcon size={19} />
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
  const { version, categories } = useApp();
  const [asOf, setAsOf] = useState(today());
  const [editor, setEditor] = useState<Entity | null | undefined>();
  const [removed, setRemoved] = useState<Entity | null>(null);
  const [projection, setProjection] = useState(false);
  const [showEnded, setShowEnded] = useState(true);
  const [excludedCategories, setExcludedCategories] = useState<string[]>([]);
  const categoryDropdown = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    function closeOutside(event: Event) {
      const dropdown = categoryDropdown.current;
      if (
        dropdown &&
        event.target instanceof Node &&
        !dropdown.contains(event.target)
      ) {
        dropdown.open = false;
      }
    }
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("focusin", closeOutside);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("focusin", closeOutside);
    };
  }, []);
  const fields = useFields("budgets");
  const summary = useLoad<any>(
    `/budget-summary?as_of=${asOf}`,
    { items: [], total_budget: "0", total_actual: "0", total_allowance: "0" },
    version,
  );
  const [budgets, setBudgets] = useState<{
    items: Entity[];
    loading: boolean;
    error: string;
  }>({ items: [], loading: true, error: "" });
  useEffect(() => {
    let active = true;
    setBudgets((current) => ({ ...current, loading: true, error: "" }));
    async function load() {
      const items: Entity[] = [];
      for (let page = 1; ; page++) {
        const result = await api<{ items: Entity[]; total: number }>(
          `/budgets?page_size=100&page=${page}`,
        );
        items.push(...result.items);
        if (!result.items.length || items.length >= result.total) break;
      }
      if (active) setBudgets({ items, loading: false, error: "" });
    }
    load().catch((e) => {
      if (active)
        setBudgets({ items: [], loading: false, error: errorMessage(e) });
    });
    return () => {
      active = false;
    };
  }, [version]);
  const names: Record<string, string> = Object.fromEntries(
    categories.map((item: Entity) => [item.id, item.name]),
  );
  const filterCategories: Entity[] = [...categories].sort(
    (a: Entity, b: Entity) => a.name.localeCompare(b.name),
  );
  const selectedCount = filterCategories.filter(
    (category) => !excludedCategories.includes(category.id),
  ).length;
  const visibleBudgets = budgets.items.filter(
    (record) =>
      (showEnded || !record.end_date || record.end_date >= today()) &&
      !excludedCategories.includes(record.category_id),
  );
  return (
    <>
      <PageHead
        title="Budgets and allowances"
        subtitle="Recurring credits minus category expenses. Balances carry forward after a budget ends."
        action={
          <>
            <input
              aria-label="Allowance date"
              type="date"
              required
              value={asOf}
              onChange={(e) => {
                if (e.target.value) setAsOf(e.target.value);
              }}
            />
            <Button
              variant="secondary"
              disabled={
                budgets.loading || !!budgets.error || !budgets.items.length
              }
              onClick={() => setProjection(true)}
            >
              Projection
            </Button>
            <AddButton onClick={() => setEditor(null)}>New budget</AddButton>
          </>
        }
      />
      <div className="stats three">
        <Stat
          label="Budget credits to date"
          value={money(summary.data.total_budget)}
          accent
        />
        <Stat
          label="Net expenses to date"
          value={money(summary.data.total_actual)}
        />
        <Stat
          label="Allowance"
          value={money(summary.data.total_allowance)}
          hint={`As of ${prettyDate(asOf)}`}
        />
      </div>
      <Panel
        title="Allowances"
        subtitle="Bars compare spending so far in the selected month with that month's full expected budget credits. Allowance balances carry forward. Refunds reduce expenses."
      >
        <ErrorBox error={summary.error} />
        {summary.loading ? (
          <Loading />
        ) : summary.data.items.length ? (
          <div className="budget-list">
            {summary.data.items.map((item: any) => (
              <div className="budget-row" key={item.category_id}>
                <div className="budget-main">
                  <div className="budget-title">
                    <strong>{item.name}</strong>
                    <span
                      className={Number(item.remaining) < 0 ? "negative" : ""}
                    >
                      {money(item.remaining)}
                    </span>
                  </div>
                  <BudgetSpendingBar item={item} />
                  <div className="budget-caption">
                    <span>{money(item.budget)} credited</span>
                    <span>{money(item.actual)} net expenses</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            title="No allowances yet"
            message="Create a budget to start tracking a category."
          />
        )}
      </Panel>
      <Panel
        title="Budget schedules"
        className="budget-schedules"
        subtitle="One active budget per category at a time. End a schedule to preserve its credits and start another later."
      >
        <div className="budget-schedule-filters">
          <Button
            variant="secondary"
            aria-pressed={showEnded}
            onClick={() => setShowEnded((current) => !current)}
          >
            {showEnded ? "Hide ended budgets" : "Show ended budgets"}
          </Button>
          <details
            ref={categoryDropdown}
            className="budget-category-filter"
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.currentTarget.open = false;
                event.currentTarget.querySelector("summary")?.focus();
              }
            }}
          >
            <summary className="button secondary">
              Categories (
              {selectedCount === filterCategories.length
                ? "All"
                : selectedCount}
              ) <ChevronRight size={14} />
            </summary>
            <div className="budget-category-options">
              <label>
                <input
                  type="checkbox"
                  checked={selectedCount === filterCategories.length}
                  onChange={(event) =>
                    setExcludedCategories(
                      event.target.checked
                        ? []
                        : filterCategories.map((category) => category.id),
                    )
                  }
                />
                All categories
              </label>
              {filterCategories.map((category) => (
                <label key={category.id}>
                  <input
                    type="checkbox"
                    checked={!excludedCategories.includes(category.id)}
                    onChange={(event) =>
                      setExcludedCategories((current) =>
                        event.target.checked
                          ? current.filter((id) => id !== category.id)
                          : [...current, category.id],
                      )
                    }
                  />
                  {category.name}
                </label>
              ))}
              {!filterCategories.length && <p>No categories yet.</p>}
            </div>
          </details>
        </div>
        <ErrorBox error={budgets.error} />
        {budgets.loading ? (
          <Loading />
        ) : visibleBudgets.length ? (
          <div className="budget-list">
            {visibleBudgets.map((record: Entity) => (
              <div className="budget-row" key={record.id}>
                <div className="budget-main">
                  <div className="budget-title">
                    <strong>{names[record.category_id] || "Category"}</strong>
                    <span>
                      {money(record.amount)} every {record.frequency}{" "}
                      {record.period}
                      {record.frequency === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div className="budget-caption">
                    <span>
                      {prettyDate(record.start_date)} –{" "}
                      {record.end_date
                        ? prettyDate(record.end_date)
                        : "No end date"}
                    </span>
                    <span>
                      {record.start_date > today()
                        ? "Upcoming"
                        : record.end_date && record.end_date < today()
                          ? "Ended"
                          : "Active"}
                    </span>
                  </div>
                </div>
                <div className="budget-actions">
                  <Button variant="text" onClick={() => setEditor(record)}>
                    Edit
                  </Button>
                  <button
                    className="icon-button subtle-danger"
                    title="Delete budget and its credits"
                    onClick={() => setRemoved(record)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            title={
              budgets.items.length
                ? "No schedules match your filters"
                : "Create your first budget"
            }
            message={
              budgets.items.length
                ? "Show ended budgets or select more categories to see their schedules."
                : "Choose a category, amount, and recurrence."
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
      )}
      {removed && (
        <DeleteDialog
          resource="budgets"
          record={removed}
          onClose={() => setRemoved(null)}
        />
      )}
      {projection && (
        <BudgetProjection
          budgets={budgets.items}
          names={names}
          onClose={() => setProjection(false)}
        />
      )}
    </>
  );
}

function BudgetSpendingBar({ item }: { item: any }) {
  const spent = Number(item.monthly_actual);
  const expected = Number(item.monthly_budget);
  const percent =
    expected > 0
      ? Math.max(0, (spent / expected) * 100)
      : spent > 0
        ? Infinity
        : 0;
  const width = Math.min(percent, 100);
  const color =
    percent > 100 ? "#ee7d62" : percent >= 80 ? "#dee302" : "#315b46";
  const description = `${money(item.monthly_actual)} spent this month of ${money(item.monthly_budget)} expected${Number.isFinite(percent) ? ` (${percent.toFixed(1)}%)` : " (no credits expected)"}`;
  return (
    <>
      <div
        className="budget-spending-bar"
        role="progressbar"
        aria-label={`${item.name} monthly spending`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={width}
        aria-valuetext={description}
        title={description}
      >
        <span style={{ width: `${width}%`, backgroundColor: color }} />
      </div>
      <div className="budget-caption">
        <span>{money(item.monthly_actual)} spent this month</span>
        <span>of {money(item.monthly_budget)} expected</span>
      </div>
    </>
  );
}

function BudgetProjection({
  budgets,
  names,
  onClose,
}: {
  budgets: Entity[];
  names: Record<string, string>;
  onClose: () => void;
}) {
  const tomorrow = new Date(`${today()}T12:00:00`);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const minDate = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, "0")}-${String(tomorrow.getDate()).padStart(2, "0")}`;
  const [date, setDate] = useState(minDate);
  const [selected, setSelected] = useState<string[]>(budgets.map((b) => b.id));
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function calculate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(
        await api("/budget-projections", {
          method: "POST",
          body: JSON.stringify({ date, budget_ids: selected }),
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      wide
      title="Budget projection"
      subtitle="Estimate future allowances using selected budget credits and scheduled subscriptions. Projections are not saved."
      onClose={onClose}
    >
      <form className="budget-projection-form" onSubmit={calculate}>
        <Field label="Future date">
          <input
            type="date"
            required
            disabled={busy}
            min={minDate}
            value={date}
            onChange={(e) => {
              setDate(e.target.value);
              setResult(null);
            }}
          />
        </Field>
        <fieldset
          className="plain-fieldset budget-projection-options"
          disabled={busy}
        >
          <legend>Include budgets</legend>
          {budgets.map((b) => (
            <label className="budget-projection-option" key={b.id}>
              <input
                type="checkbox"
                checked={selected.includes(b.id)}
                onChange={(e) => {
                  setSelected((current) =>
                    e.target.checked
                      ? [...current, b.id]
                      : current.filter((id) => id !== b.id),
                  );
                  setResult(null);
                }}
              />
              {names[b.category_id] || "Category"}: {money(b.amount)} every{" "}
              {b.frequency} {b.period}
              {b.frequency === 1 ? "" : "s"} ({prettyDate(b.start_date)} –{" "}
              {b.end_date ? prettyDate(b.end_date) : "ongoing"})
            </label>
          ))}
        </fieldset>
        <p className="muted">
          Existing allowances are carried forward. Unchecked schedules add no
          future credits. Future recorded expenses are included; linked
          subscription payments are counted once. Subscription estimates use
          current exchange rates.
        </p>
        <ErrorBox error={error} />
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button busy={busy} disabled={!selected.length}>
            Calculate projection
          </Button>
        </div>
      </form>
      {result && (
        <div className="budget-projection-results" aria-live="polite">
          <h3>Estimated allowances on {prettyDate(result.date)}</h3>
          {result.items.map((item: any) => (
            <div className="budget-row" key={item.category_id}>
              <div className="budget-main">
                <div className="budget-title">
                  <strong>{item.name}</strong>
                  <span
                    className={
                      Number(item.projected_allowance) < 0 ? "negative" : ""
                    }
                  >
                    {money(item.projected_allowance)}
                  </span>
                </div>
                <div className="budget-caption">
                  <span>
                    Current {money(item.current_allowance)} + credits{" "}
                    {money(item.future_credits)}
                  </span>
                  <span>
                    Recorded {money(item.recorded_expenses)} + subscriptions{" "}
                    {money(item.subscription_expenses)}
                  </span>
                </div>
                {item.warnings.map((warning: string) => (
                  <p className="negative" key={warning}>
                    {warning}
                  </p>
                ))}
              </div>
            </div>
          ))}
          {result.incomplete && (
            <p className="negative">
              Estimate is incomplete because exchange rates are missing.
            </p>
          )}
        </div>
      )}
    </Modal>
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
                <HoardEggIcon size={82} strokeWidth={0.8} />
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
  function updateFlow(
    index: number,
    changes: Record<string, string | undefined>,
  ) {
    setFlows((current) =>
      current.map((flow, i) => (i === index ? { ...flow, ...changes } : flow)),
    );
  }
  function tomorrow() {
    const day = new Date(`${today()}T12:00:00`);
    day.setDate(day.getDate() + 1);
    return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  }
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
                  r.currency ||
                    accounts.find((a: Entity) => a.id === r.account_id)
                      ?.currency,
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
                  projected accounts. Recurring amounts apply on each scheduled
                  date, including the end date, up to the projection horizon.
                </p>
                {flows.map((f, i) => (
                  <div className="flow-row" key={i}>
                    <Field label="Account">
                      <select
                        aria-label="Flow account"
                        required
                        value={f.account_id}
                        onChange={(e) =>
                          updateFlow(i, { account_id: e.target.value })
                        }
                      >
                        <option value="">Select account…</option>
                        {owned.map((a: Entity) => (
                          <option key={a.id} value={a.id}>
                            {a.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Amount in USD">
                      <input
                        aria-label="Flow amount in USD"
                        required
                        inputMode="decimal"
                        placeholder="USD amount"
                        value={f.amount}
                        onChange={(e) =>
                          updateFlow(i, { amount: e.target.value })
                        }
                      />
                    </Field>
                    <Field label="Frequency">
                      <select
                        aria-label="Flow frequency"
                        value={f.frequency || "once"}
                        onChange={(e) =>
                          updateFlow(i, {
                            frequency: e.target.value,
                            end_date:
                              e.target.value === "once"
                                ? undefined
                                : f.end_date || date,
                          })
                        }
                      >
                        <option value="once">One time</option>
                        <option value="daily">Daily</option>
                        <option value="weekly">Weekly</option>
                        <option value="monthly">Monthly</option>
                        <option value="quarterly">Quarterly</option>
                        <option value="yearly">Yearly</option>
                      </select>
                    </Field>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Remove flow"
                      onClick={() => setFlows(flows.filter((_, j) => i !== j))}
                    >
                      <Trash2 size={16} />
                    </button>
                    <div className="flow-dates">
                      <Field
                        label={
                          f.frequency && f.frequency !== "once"
                            ? "Start date"
                            : "Date"
                        }
                      >
                        <input
                          aria-label={
                            f.frequency && f.frequency !== "once"
                              ? "Flow start date"
                              : "Flow date"
                          }
                          required
                          type="date"
                          min={
                            !f.frequency || f.frequency === "once"
                              ? tomorrow()
                              : undefined
                          }
                          max={date}
                          value={f.date}
                          onChange={(e) =>
                            updateFlow(i, { date: e.target.value })
                          }
                        />
                      </Field>
                      {f.frequency && f.frequency !== "once" && (
                        <Field
                          label="End date"
                          hint="Includes this date; projection stops at your chosen horizon."
                        >
                          <input
                            aria-label="Flow end date"
                            required
                            type="date"
                            min={f.date}
                            value={f.end_date || ""}
                            onChange={(e) =>
                              updateFlow(i, { end_date: e.target.value })
                            }
                          />
                        </Field>
                      )}
                    </div>
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
                        date: tomorrow(),
                        frequency: "once",
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
          <PerformanceCalculation data={data} />
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

function PerformanceCalculation({ data }: { data: any }) {
  const percent = (value: string) => `${(Number(value) * 100).toFixed(2)}%`;
  const factor = (value: string) => (1 + Number(value)).toFixed(6);
  return (
    <div className="performance-calculation panel-body">
      <h3>How your returns were calculated</h3>
      <p>
        All amounts are in USD, using the exchange rates saved with each
        valuation and transaction. We use consecutive valuations within the
        latest three years, ending at the most recent valuation on or before
        today.
      </p>
      <p>
        Contributions are positive and withdrawals are negative. Cash flows
        after the opening valuation through the closing valuation are included.
        Valuation gains, losses, and investment fees stay in performance;
        opening principal and balance corrections count as external cash flows.
      </p>
      <h4>1. Calculate each valuation period</h4>
      <p>
        Each cash flow is weighted by how long it was invested: weight = days
        remaining until the closing valuation ÷ days in the period. A cash flow
        on the closing date has a weight of zero.
      </p>
      {data.intervals.map((interval: any, index: number) => (
        <details
          className="performance-period"
          key={`${interval.start}-${interval.end}`}
          open={index === 0}
        >
          <summary>
            {prettyDate(interval.start)} – {prettyDate(interval.end)} ·{" "}
            {interval.days} days · {percent(interval.return_rate)} return
          </summary>
          <dl className="performance-inputs">
            <div>
              <dt>Starting valuation</dt>
              <dd>{money(interval.starting_value)}</dd>
            </div>
            <div>
              <dt>Ending valuation</dt>
              <dd>{money(interval.ending_value)}</dd>
            </div>
            <div>
              <dt>Net external cash flows</dt>
              <dd>{money(interval.external_flows)}</dd>
            </div>
            <div>
              <dt>Weighted cash flows</dt>
              <dd>{money(interval.weighted_flows)}</dd>
            </div>
          </dl>
          {interval.cash_flows.length ? (
            <div className="table-wrap">
              <table>
                <caption>Cash-flow weighting for this period</caption>
                <thead>
                  <tr>
                    <th>Date / cash flow</th>
                    <th className="number">Amount (USD)</th>
                    <th className="number">
                      Weight (days remaining / period days)
                    </th>
                    <th className="number">Amount × weight (USD)</th>
                  </tr>
                </thead>
                <tbody>
                  {interval.cash_flows.map((flow: any) => (
                    <tr key={flow.id}>
                      <td>
                        {prettyDate(flow.date)} · {flow.kind}
                        <br />
                        <span className="muted">{flow.description}</span>
                      </td>
                      <td className="number">{money(flow.amount)}</td>
                      <td className="number">
                        {flow.remaining_days} / {interval.days} ={" "}
                        {Number(flow.weight).toFixed(6)}
                      </td>
                      <td className="number">{money(flow.weighted_amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>
              No external cash flows in this period; net and weighted cash flows
              are both $0.00.
            </p>
          )}
          <p>
            Net cash flows = sum of signed cash-flow amounts. Weighted cash
            flows = sum of each amount × its weight.
          </p>
          <ol className="performance-steps">
            <li>
              Investment gain = ending value − starting value − net cash flows
              <div>
                {money(interval.ending_value)} − (
                {money(interval.starting_value)}) − (
                {money(interval.external_flows)}) ={" "}
                <strong>{money(interval.gain)}</strong>
              </div>
            </li>
            <li>
              Weighted capital = starting value + weighted cash flows
              <div>
                {money(interval.starting_value)} + (
                {money(interval.weighted_flows)}) ={" "}
                <strong>{money(interval.weighted_capital)}</strong>
              </div>
            </li>
            <li>
              Period return = investment gain ÷ weighted capital
              <div>
                {money(interval.gain)} ÷ {money(interval.weighted_capital)} ={" "}
                <strong>{percent(interval.return_rate)}</strong>
              </div>
            </li>
          </ol>
        </details>
      ))}
      <h4>2. Combine the period returns</h4>
      <p>
        Cumulative return = (1 + first period return) × (1 + next period return)
        × … − 1.
      </p>
      <p className="performance-formula">
        {data.intervals
          .map((interval: any) => factor(interval.return_rate))
          .join(" × ")}{" "}
        − 1 = <strong>{percent(data.cumulative_return)}</strong>
      </p>
      <h4>3. Convert to an annual rate</h4>
      <p>
        Annualized return = (1 + cumulative return) raised to (365.25 ÷ coverage
        days) − 1.
      </p>
      {data.annualized_return == null ? (
        <p>
          There are {data.days} days of coverage. At least 365 days are needed
          to show an annualized return.
        </p>
      ) : (
        <p className="performance-formula">
          {factor(data.cumulative_return)} ^ (365.25 ÷ {data.days}) − 1 ={" "}
          <strong>{percent(data.annualized_return)}</strong>
        </p>
      )}
      <p className="muted">
        Displayed amounts and rates are rounded. Calculations use the full
        stored precision.
      </p>
    </div>
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
                {["Income", "Expense", "Transfer"].map((t) => (
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
            <div className="stats three">
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
