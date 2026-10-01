"use client";

import { useState } from "react";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowUpRight,
  ChevronRight,
  Search,
  SlidersHorizontal,
  Pencil,
  Trash2,
  Landmark,
  Wallet,
  CreditCard,
  Scale,
  Plus,
} from "lucide-react";
import {
  api,
  Entity,
  ListResult,
  errorMessage,
  decimalSum,
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
  Pagination,
  Panel,
  Stat,
  TypeBadge,
  useApp,
  useLoad,
} from "./ui";
import {
  FieldSpec,
  RecordEditor,
  ReconcileEditor,
  RuleEditor,
  TransactionEditor,
} from "./editors";

export function useFields(resource: string): FieldSpec[] {
  const { accounts, categories, meta } = useApp();
  const accountOptions = accounts.map((a: Entity) => ({
    value: a.id,
    label: `${a.name} (${a.currency})`,
  }));
  const categoryOptions = categories.map((c: Entity) => ({
    value: c.id,
    label: c.name,
  }));
  const currencyOptions = (
    meta?.currencies || [
      { code: "USD" },
      { code: "EUR" },
      { code: "GBP" },
      { code: "BTC" },
      { code: "ETH" },
      { code: "JPY" },
    ]
  ).map((c: any) => ({
    value: typeof c === "string" ? c : c.code,
    label: typeof c === "string" ? c : c.code,
  }));
  const specs: Record<string, FieldSpec[]> = {
    accounts: [
      { name: "name", label: "Account name", required: true, wide: true },
      {
        name: "type",
        label: "Account type",
        type: "select",
        required: true,
        default: "Checking",
        options: (
          meta?.account_types || [
            "Checking",
            "Savings",
            "Brokerage",
            "Roth IRA",
            "Traditional IRA",
            "Roth 401K",
            "Traditional 401K",
            "HSA",
            "FSA",
            "529",
            "Wallet",
            "Crypto Cold Wallet",
            "Credit Card",
            "Loan",
            "Mortgage",
            "Expense Payee",
            "Income Source",
            "Equity",
          ]
        ).map((v: string) => ({ value: v, label: v })),
      },
      {
        name: "account_class",
        label: "Accounting class",
        type: "select",
        required: true,
        default: "asset",
        options: ["asset", "liability", "income", "expense", "equity"].map(
          (v) => ({
            value: v,
            label:
              {
                asset: "Asset — money you own",
                liability: "Liability — money you owe",
                income: "Income — salary or earnings source",
                expense: "Expense — merchant or payee",
                equity: "Equity — adjustments",
              }[v] || v,
          }),
        ),
      },
      {
        name: "currency",
        label: "Currency",
        type: "select",
        required: true,
        default: "USD",
        options: currencyOptions,
      },
      {
        name: "opening_balance",
        label: "Opening balance",
        type: "decimal",
        default: "0",
        hint: "For debt, enter the positive amount owed.",
      },
      {
        name: "opening_date",
        label: "Opening date",
        type: "date",
        required: true,
        default: today(),
      },
    ],
    categories: [
      { name: "name", label: "Category name", required: true },
      { name: "color", label: "Color", type: "color", default: "#315b46" },
    ],
    currencies: [
      { name: "code", label: "Three-letter currency code", required: true },
      { name: "name", label: "Currency name", required: true },
      {
        name: "precision",
        label: "Decimal precision",
        type: "integer",
        required: true,
        default: 2,
        min: "0",
      },
    ],
    tags: [{ name: "name", label: "Tag name", required: true }],
    rates: [
      {
        name: "currency",
        label: "Currency",
        type: "select",
        required: true,
        options: currencyOptions,
      },
      {
        name: "date",
        label: "Effective date",
        type: "date",
        required: true,
        default: today(),
      },
      {
        name: "rate",
        label: "USD per currency unit",
        type: "decimal",
        required: true,
        hint: "Example: 1 EUR = 1.10 USD → enter 1.10.",
        wide: true,
      },
    ],
    valuations: [
      {
        name: "account_id",
        label: "Investment account",
        type: "select",
        required: true,
        options: accountOptions,
      },
      {
        name: "date",
        label: "Valuation date",
        type: "date",
        required: true,
        default: today(),
      },
      {
        name: "amount",
        label: "Closing market value",
        type: "decimal",
        required: true,
        hint: "Include cash. Enter the value in the account’s native currency.",
        wide: true,
      },
    ],
    budgets: [
      {
        name: "month",
        label: "Month",
        type: "month",
        required: true,
        default: monthNow(),
      },
      {
        name: "category_id",
        label: "Category",
        type: "select",
        required: true,
        options: categoryOptions,
      },
      {
        name: "amount",
        label: "Monthly target (USD)",
        type: "decimal",
        required: true,
        wide: true,
      },
    ],
    users: [
      { name: "username", label: "Username", required: true },
      {
        name: "password",
        label: "Password",
        type: "password",
        hint: "At least 12 characters. Leave blank when editing to keep the password.",
      },
      {
        name: "role",
        label: "Role",
        type: "select",
        required: true,
        default: "member",
        options: [
          { value: "member", label: "Member" },
          { value: "admin", label: "Administrator" },
        ],
      },
      {
        name: "active",
        label: "Account access",
        type: "checkbox",
        default: true,
      },
    ],
    subscriptions: [
      { name: "name", label: "Subscription name", required: true, wide: true },
      {
        name: "type",
        label: "Transaction type",
        type: "select",
        required: true,
        default: "Expense",
        options: ["Expense", "Income", "Transfer", "Return"].map((v) => ({
          value: v,
          label: v,
        })),
      },
      {
        name: "currency",
        label: "Currency",
        type: "select",
        required: true,
        default: "USD",
        options: currencyOptions,
      },
      {
        name: "source_account_id",
        label: "Source account",
        type: "select",
        required: true,
        options: accountOptions,
      },
      {
        name: "destination_account_id",
        label: "Destination account",
        type: "select",
        required: true,
        options: accountOptions,
      },
      {
        name: "amount",
        label: "Expected amount",
        type: "decimal",
        required: true,
      },
      {
        name: "category_id",
        label: "Category",
        type: "select",
        options: categoryOptions,
      },
      {
        name: "frequency",
        label: "Frequency",
        type: "select",
        required: true,
        default: "monthly",
        options: ["daily", "weekly", "monthly", "yearly"].map((v) => ({
          value: v,
          label: v,
        })),
      },
      {
        name: "interval",
        label: "Every",
        type: "integer",
        default: 1,
        min: "1",
        required: true,
      },
      {
        name: "start_date",
        label: "First due date",
        type: "date",
        default: today(),
        required: true,
      },
      { name: "end_date", label: "End date (optional)", type: "date" },
      {
        name: "amount_tolerance",
        label: "Amount matching tolerance",
        type: "decimal",
        default: "0",
      },
      {
        name: "day_tolerance",
        label: "Date matching window (± days)",
        type: "integer",
        default: 7,
        min: "0",
      },
    ],
  };
  return (specs[resource] || []).filter(
    (field) => resource !== "accounts" || field.name !== "account_class",
  );
}

export function DeleteDialog({
  resource,
  record,
  onClose,
  onSaved,
}: {
  resource: string;
  record: Entity;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const { notify, refresh } = useApp();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function remove() {
    setBusy(true);
    try {
      await api(`/${resource}/${record.id}`, {
        method: "DELETE",
        body: JSON.stringify({ revision: record.revision }),
      });
      notify(
        resource === "transactions"
          ? "Transaction voided."
          : resource === "users"
            ? "Member deactivated."
            : "Record removed.",
      );
      refresh();
      onSaved?.();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={
        resource === "transactions"
          ? "Void transaction?"
          : resource === "users"
            ? "Deactivate this member?"
            : "Remove this record?"
      }
      onClose={onClose}
    >
      <p className="dialog-copy">
        {resource === "transactions"
          ? "This removes the transaction from active balances and reports. Its original values remain in the audit history."
          : `Remove “${record.name || record.description || record.date || "this record"}”? Records in use may need to be unlinked first.`}
      </p>
      <ErrorBox error={error} />
      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          Keep record
        </Button>
        <Button variant="danger" busy={busy} onClick={remove}>
          {resource === "transactions"
            ? "Void transaction"
            : resource === "users"
              ? "Deactivate"
              : "Remove record"}
        </Button>
      </div>
    </Modal>
  );
}

export function TransactionTable({
  items,
  onEdit,
  onDelete,
}: {
  items: Entity[];
  onEdit: (r: Entity) => void;
  onDelete?: (r: Entity) => void;
}) {
  const { accounts, categories } = useApp();
  const name = (id: string) =>
    accounts.find((a: Entity) => a.id === id)?.name || "—";
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Transaction</th>
            <th>Date</th>
            <th>Category</th>
            <th>Account</th>
            <th className="number">Amount</th>
            <th>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id}>
              <td>
                <button className="table-link" onClick={() => onEdit(t)}>
                  {t.description}
                </button>
                <div className="cell-sub">
                  <TypeBadge type={t.type} refund={t.is_refund} />
                  {t.subscription_id && (
                    <span
                      className="recurring-dot"
                      title="Linked to subscription"
                    >
                      Recurring
                    </span>
                  )}
                </div>
              </td>
              <td className="nowrap">{prettyDate(t.date)}</td>
              <td>
                <span className="category-chip">
                  {categories.find((c: Entity) => c.id === t.category_id)
                    ?.name || "Uncategorized"}
                </span>
              </td>
              <td>
                <span className="account-cell">
                  {name(
                    t.type === "Income" || t.type === "Return"
                      ? t.destination_account_id
                      : t.source_account_id,
                  )}
                </span>
                <div className="cell-sub">
                  to {name(t.destination_account_id)}
                </div>
              </td>
              <td
                className={`number amount ${t.type === "Income" || t.type === "Return" || t.is_refund ? "positive" : ""}`}
              >
                {t.type === "Expense" && !t.is_refund
                  ? "−"
                  : t.type === "Transfer"
                    ? ""
                    : "+"}
                {money(
                  t.amount,
                  t.currency ||
                    accounts.find((a: Entity) => a.id === t.source_account_id)
                      ?.currency ||
                    "USD",
                )}
                {t.currency && t.currency !== "USD" && (
                  <div className="cell-sub">{money(t.usd_amount)}</div>
                )}
              </td>
              <td className="row-actions">
                <button
                  className="icon-button"
                  aria-label={`Edit ${t.description}`}
                  onClick={() => onEdit(t)}
                >
                  <Pencil size={15} />
                </button>
                {onDelete && (
                  <button
                    className="icon-button subtle-danger"
                    aria-label={`Void ${t.description}`}
                    onClick={() => onDelete(t)}
                  >
                    <Trash2 size={15} />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Transactions({
  accountId,
  embedded = false,
}: {
  accountId?: string;
  embedded?: boolean;
}) {
  const { accounts, categories, tags, subscriptions, version } = useApp();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Record<string, string>>({
    account_id: accountId || "",
    q: "",
    type: "",
    category_id: "",
    tag_id: "",
    subscription_id: "",
    currency: "",
    start: "",
    end: "",
  });
  const [advanced, setAdvanced] = useState(false);
  const [editor, setEditor] = useState<Entity | null | undefined>();
  const [removed, setRemoved] = useState<Entity | null>(null);
  const { data, loading, error } = useLoad<ListResult>(
    `/transactions?${query({ ...filters, page, page_size: 25 })}`,
    { items: [], total: 0, page: 1, page_size: 25 },
    version,
  );
  const change = (key: string, value: string) => {
    setFilters({ ...filters, [key]: value });
    setPage(1);
  };
  return (
    <>
      {!embedded && (
        <PageHead
          title="Transactions"
          subtitle="Every movement, thoughtfully accounted for."
          action={
            <AddButton onClick={() => setEditor(null)}>
              Add transaction
            </AddButton>
          }
        />
      )}
      <Panel
        title={embedded ? "Account activity" : undefined}
        action={
          embedded ? (
            <Button variant="secondary" onClick={() => setEditor(null)}>
              <Plus size={15} />
              Add transaction
            </Button>
          ) : undefined
        }
      >
        <div className="toolbar">
          <label className="search-field">
            <Search size={17} />
            <input
              aria-label="Search transactions"
              placeholder="Search transactions…"
              value={filters.q}
              onChange={(e) => change("q", e.target.value)}
            />
          </label>
          {!accountId && (
            <select
              aria-label="Filter account"
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
          )}
          <select
            aria-label="Filter transaction type"
            value={filters.type}
            onChange={(e) => change("type", e.target.value)}
          >
            <option value="">All types</option>
            {["Income", "Expense", "Transfer", "Return"].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <Button
            variant={advanced ? "soft" : "secondary"}
            onClick={() => setAdvanced(!advanced)}
          >
            <SlidersHorizontal size={16} />
            Filters
          </Button>
          <a
            className="button secondary icon-only"
            title="Export filtered transactions"
            href={`/api/v1/reports/csv?${query(filters)}`}
          >
            <ArrowDownToLine size={17} />
          </a>
        </div>
        {advanced && (
          <div className="filter-grid">
            <Field label="From">
              <input
                type="date"
                value={filters.start}
                onChange={(e) => change("start", e.target.value)}
              />
            </Field>
            <Field label="Through">
              <input
                type="date"
                value={filters.end}
                onChange={(e) => change("end", e.target.value)}
              />
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
                placeholder="USD, EUR…"
                value={filters.currency}
                onChange={(e) =>
                  change("currency", e.target.value.toUpperCase())
                }
              />
            </Field>
          </div>
        )}
        <ErrorBox error={error} />
        {loading ? (
          <Loading />
        ) : data.items.length ? (
          <TransactionTable
            items={data.items}
            onEdit={setEditor}
            onDelete={setRemoved}
          />
        ) : (
          <Empty
            title="A fresh page in your ledger"
            message="Record an expense, income, transfer, or investment return."
            action={
              <AddButton onClick={() => setEditor(null)}>
                Add transaction
              </AddButton>
            }
          />
        )}
        <Pagination page={page} total={data.total} onChange={setPage} />
      </Panel>
      {editor !== undefined && (
        <TransactionEditor
          record={editor || undefined}
          preset={accountId ? { source_account_id: accountId } : undefined}
          onClose={() => setEditor(undefined)}
        />
      )}{" "}
      {removed && (
        <DeleteDialog
          resource="transactions"
          record={removed}
          onClose={() => setRemoved(null)}
        />
      )}
    </>
  );
}

export function Accounts() {
  const { accounts, version } = useApp();
  const fields = useFields("accounts");
  const [editor, setEditor] = useState<Entity | null | undefined>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reconcile, setReconcile] = useState<Entity | null>(null);
  const [removed, setRemoved] = useState<Entity | null>(null);
  const [group, setGroup] = useState("owned");
  const [typeFilter, setTypeFilter] = useState("");
  const valuations = useLoad<ListResult>(
    "/valuations?" + query({ account_id: selectedId, page_size: 500 }),
    { items: [], total: 0, page: 1, page_size: 500 },
    version,
  );
  const selected = accounts.find((a: Entity) => a.id === selectedId);
  const owned = accounts.filter(
    (a: Entity) =>
      ["asset", "liability"].includes(a.account_class) &&
      (!typeFilter || a.type === typeFilter),
  );
  const asset = decimalSum(
      owned
        .filter((a: Entity) => a.account_class === "asset")
        .map((a: Entity) => a.balance_usd),
    ),
    liability = decimalSum(
      owned
        .filter((a: Entity) => a.account_class === "liability")
        .map((a: Entity) => a.balance_usd),
    );
  const shown = accounts.filter(
    (a: Entity) =>
      (group === "owned"
        ? ["asset", "liability"].includes(a.account_class)
        : !["asset", "liability"].includes(a.account_class)) &&
      (!typeFilter || a.type === typeFilter),
  );
  return (
    <>
      {selected ? (
        <>
          <button className="back-link" onClick={() => setSelectedId(null)}>
            <ArrowLeft size={16} />
            All accounts
          </button>
          <PageHead
            title={selected.name}
            subtitle={`${selected.type} · ${selected.currency}`}
            action={
              !selected.system && (
                <>
                  <Button
                    variant="secondary"
                    onClick={() => setEditor(selected)}
                  >
                    <Pencil size={15} />
                    Edit account
                  </Button>
                  <Button onClick={() => setReconcile(selected)}>
                    <Scale size={16} />
                    Reconcile
                  </Button>
                </>
              )
            }
          />
          <div className="stats three">
            <Stat
              label={
                selected.account_class === "liability"
                  ? "Amount owed"
                  : "Current balance"
              }
              value={money(selected.balance, selected.currency)}
              accent
            />
            <Stat
              label="USD equivalent"
              value={
                selected.balance_usd == null
                  ? "Rate needed"
                  : money(selected.balance_usd)
              }
            />
            <Stat
              label="Last valuation"
              value={
                <span className="small-stat">
                  {prettyDate(
                    selected.valuation_date !== undefined
                      ? selected.valuation_date
                      : [
                          selected.last_valuation_date,
                          ...valuations.data.items
                            .filter((v: Entity) => v.account_id === selected.id)
                            .map((v: Entity) => v.date),
                        ]
                          .filter(
                            (date) =>
                              date &&
                              (!selected.as_of || date <= selected.as_of),
                          )
                          .sort()
                          .at(-1),
                  )}
                </span>
              }
              hint="End-of-day market value"
            />
          </div>
          <Transactions key={selected.id} accountId={selected.id} embedded />
        </>
      ) : (
        <>
          <PageHead
            title="Your accounts"
            subtitle="A home for everything you hold — and everything you owe."
            action={
              <AddButton onClick={() => setEditor(null)}>Add account</AddButton>
            }
          />
          {owned.some((a: Entity) => a.balance_usd == null) && (
            <div className="notice">
              Some accounts need exchange rates. The totals below include only
              accounts with an available USD value.
            </div>
          )}
          <div className="stats three">
            <Stat
              label="Total assets"
              value={money(asset)}
              accent
              icon={<Landmark size={19} />}
            />
            <Stat
              label="Total liabilities"
              value={money(liability)}
              icon={<CreditCard size={19} />}
            />
            <Stat
              label="Net worth"
              value={money(
                decimalSum([
                  asset,
                  liability.startsWith("-")
                    ? liability.slice(1)
                    : "-" + liability,
                ]),
              )}
              icon={<SproutIcon />}
            />
          </div>
          <div className="section-header">
            <div className="tabs">
              <button
                className={group === "owned" ? "active" : ""}
                onClick={() => {
                  setGroup("owned");
                  setTypeFilter("");
                }}
              >
                Financial accounts <span>{owned.length}</span>
              </button>
              <button
                className={group === "other" ? "active" : ""}
                onClick={() => {
                  setGroup("other");
                  setTypeFilter("");
                }}
              >
                Payees & sources
              </button>
            </div>
            <select
              aria-label="Filter by account type"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All account types</option>
              {Array.from(
                new Set<string>(
                  accounts
                    .filter((a: Entity) =>
                      group === "owned"
                        ? ["asset", "liability"].includes(a.account_class)
                        : !["asset", "liability"].includes(a.account_class),
                    )
                    .map((a: Entity) => a.type),
                ),
              ).map((type) => (
                <option key={type}>{type}</option>
              ))}
            </select>
          </div>
          {shown.length ? (
            <div className="account-grid">
              {shown.map((a: Entity) => (
                <div className="account-card" key={a.id}>
                  <div className="account-card-top">
                    <div
                      className={`account-icon ${a.account_class === "liability" ? "debt" : ""}`}
                    >
                      {a.account_class === "liability" ? (
                        <CreditCard size={21} />
                      ) : a.type === "Wallet" ? (
                        <Wallet size={21} />
                      ) : (
                        <Landmark size={21} />
                      )}
                    </div>
                    <span className="account-currency">{a.currency}</span>
                    <div className="account-card-actions">
                      {!a.system && (
                        <>
                          <button
                            className="icon-button"
                            title="Edit account"
                            onClick={() => setEditor(a)}
                          >
                            <Pencil size={15} />
                          </button>
                          <button
                            className="icon-button subtle-danger"
                            title="Remove account"
                            onClick={() => setRemoved(a)}
                          >
                            <Trash2 size={15} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  <button
                    className="account-name"
                    onClick={() => setSelectedId(a.id)}
                  >
                    {a.name}
                    <ArrowUpRight size={17} />
                  </button>
                  <p>
                    {a.type}{" "}
                    {a.account_class === "liability" ? "· Amount owed" : ""}
                  </p>
                  <div className="account-balance">
                    {money(a.balance, a.currency)}
                  </div>
                  <div className="account-card-foot">
                    <span>
                      {a.currency !== "USD"
                        ? a.balance_usd == null
                          ? "Exchange rate needed"
                          : `${money(a.balance_usd)} USD`
                        : "Native balance"}
                    </span>
                    <button onClick={() => setSelectedId(a.id)}>
                      View activity <ChevronRight size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Panel>
              <Empty
                title="Bring your accounts together"
                message="Start with a checking or savings account, then add your payees and income sources."
                action={
                  <AddButton onClick={() => setEditor(null)}>
                    Create an account
                  </AddButton>
                }
              />
            </Panel>
          )}
        </>
      )}
      {editor !== undefined && (
        <RecordEditor
          resource="accounts"
          title="Account"
          fields={fields}
          record={editor || undefined}
          onClose={() => setEditor(undefined)}
        />
      )}{" "}
      {reconcile && (
        <ReconcileEditor
          account={reconcile}
          onClose={() => setReconcile(null)}
        />
      )}{" "}
      {removed && (
        <DeleteDialog
          resource="accounts"
          record={removed}
          onClose={() => setRemoved(null)}
        />
      )}
    </>
  );
}
function SproutIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="M10 17V9m0 3C3 12 3 5 3 5s7 0 7 7Zm0-3c0-6 7-6 7-6s0 6-7 6Z" />
    </svg>
  );
}

export function ResourceTable({
  resource,
  title,
  columns,
  heading = true,
  description,
  customAction,
}: {
  resource: string;
  title: string;
  columns: {
    key: string;
    label: string;
    render?: (r: Entity) => React.ReactNode;
  }[];
  heading?: boolean;
  description?: string;
  customAction?: (r: Entity) => React.ReactNode;
}) {
  const { version } = useApp();
  const fields = useFields(resource);
  const [page, setPage] = useState(1);
  const [editor, setEditor] = useState<Entity | null | undefined>();
  const [removed, setRemoved] = useState<Entity | null>(null);
  const { data, loading, error } = useLoad<ListResult>(
    `/${resource}?page=${page}&page_size=25`,
    { items: [], total: 0, page: 1, page_size: 25 },
    version,
  );
  const visibleItems = ["currencies", "users"].includes(resource)
    ? data.items.slice((page - 1) * 25, page * 25)
    : data.items;
  return (
    <>
      {heading && (
        <PageHead
          title={title}
          subtitle={description || `Manage your ${title.toLowerCase()}.`}
          action={
            <AddButton onClick={() => setEditor(null)}>
              Add{" "}
              {resource === "categories"
                ? "category"
                : resource === "currencies"
                  ? "currency"
                  : resource.replace(/s$/, "")}
            </AddButton>
          }
        />
      )}
      <Panel
        title={!heading ? title : undefined}
        subtitle={!heading ? description : undefined}
        action={
          !heading ? (
            <AddButton onClick={() => setEditor(null)}>
              Add{" "}
              {resource === "categories"
                ? "category"
                : resource === "currencies"
                  ? "currency"
                  : resource.replace(/s$/, "")}
            </AddButton>
          ) : undefined
        }
      >
        <ErrorBox error={error} />
        {loading ? (
          <Loading />
        ) : data.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c.key}>{c.label}</th>
                  ))}
                  <th>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((r) => (
                  <tr key={r.id}>
                    {columns.map((c) => (
                      <td key={c.key}>
                        {c.render
                          ? c.render(r)
                          : typeof r[c.key] === "boolean"
                            ? r[c.key]
                              ? "Enabled"
                              : "Disabled"
                            : (r[c.key] ?? "—")}
                      </td>
                    ))}
                    <td className="row-actions">
                      {customAction?.(r)}
                      {!r.builtin && (
                        <>
                          <button
                            className="icon-button"
                            title="Edit"
                            onClick={() => setEditor(r)}
                          >
                            <Pencil size={15} />
                          </button>
                          <button
                            className="icon-button subtle-danger"
                            title={
                              resource === "users" ? "Deactivate" : "Remove"
                            }
                            onClick={() => setRemoved(r)}
                          >
                            <Trash2 size={15} />
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={`No ${title.toLowerCase()} yet`}
            message={
              description ||
              `Add your first ${resource === "categories" ? "category" : resource === "currencies" ? "currency" : resource.replace(/s$/, "")} to get started.`
            }
          />
        )}
        <Pagination page={page} total={data.total} onChange={setPage} />
      </Panel>
      {editor !== undefined &&
        (resource === "rules" ? (
          <RuleEditor
            record={editor || undefined}
            onClose={() => setEditor(undefined)}
          />
        ) : (
          <RecordEditor
            resource={resource}
            title={
              resource === "categories"
                ? "Category"
                : resource === "currencies"
                  ? "Currency"
                  : resource
                      .replace(/s$/, "")
                      .replace(/^./, (c) => c.toUpperCase())
            }
            fields={fields}
            record={editor || undefined}
            onClose={() => setEditor(undefined)}
          />
        ))}
      {removed && (
        <DeleteDialog
          resource={resource}
          record={removed}
          onClose={() => setRemoved(null)}
        />
      )}
    </>
  );
}
