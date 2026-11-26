"use client";

import { useState } from "react";
import {
  compareAmounts,
  signedTransactionAmount,
  TableSort,
  toggleSort,
} from "@/lib/table-sort";
import { HoardEggIcon } from "./hoard-art";
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
  Archive,
  FileUp,
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
import { TransactionImport } from "./transaction-import";

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
        name: "description",
        label: "Description",
        type: "textarea",
        wide: true,
      },
      {
        name: "account_number",
        label: "Account number (optional)",
        hint: "Used to identify, link, or search for this account.",
        wide: true,
      },
      {
        name: "type",
        label: "Account type",
        type: "select",
        required: true,
        default: "Checking",
        options: (
          meta?.account_types || [
            "Asset",
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
                asset: "Asset — money or property you own",
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
      { name: "name", label: "Category name", required: true, maxLength: 36 },
      { name: "color", label: "Color", type: "color", default: "#315b46" },
      {
        name: "description",
        label: "Description (optional)",
        type: "textarea",
        maxLength: 120,
        wide: true,
      },
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
    tags: [{ name: "name", label: "Tag name", required: true, maxLength: 36 }],
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
        name: "currency",
        label: "Valuation currency",
        type: "select",
        options: currencyOptions,
        hint: "Leave blank to use the account’s currency.",
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
        hint: "Include cash. Enter the value in the selected valuation currency.",
        wide: true,
      },
    ],
    budgets: [
      {
        name: "category_id",
        label: "Category",
        type: "select",
        required: true,
        options: categoryOptions,
      },
      {
        name: "amount",
        label: "Amount per occurrence (USD)",
        type: "decimal",
        required: true,
      },
      {
        name: "start_date",
        label: "Start date",
        type: "date",
        required: true,
        default: today(),
        hint: "The first credit occurs on this date.",
      },
      {
        name: "end_date",
        label: "End date",
        type: "date",
        hint: "Inclusive. Leave blank to continue indefinitely.",
      },
      {
        name: "frequency",
        label: "Repeat every",
        type: "integer",
        required: true,
        default: 1,
        min: "1",
        step: "1",
      },
      {
        name: "period",
        label: "Period",
        type: "select",
        required: true,
        default: "month",
        options: [
          { value: "day", label: "Days" },
          { value: "week", label: "Weeks" },
          { value: "month", label: "Months" },
          { value: "year", label: "Years" },
        ],
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
        options: ["Expense", "Income", "Transfer"].map((v) => ({
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
          : resource === "budgets"
            ? "Deleting this budget removes all of its credits and recalculates the category allowance. To preserve past credits, edit the budget's end date instead."
            : resource === "accounts"
              ? `Remove “${record.name}” from active accounts? Only unused accounts with a zero opening balance can be removed. Audit history is retained; linked records must be unlinked first.`
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

function SortHeader({
  label,
  column,
  sort,
  onSort,
  numeric = false,
}: {
  label: string;
  column: string;
  sort: TableSort | null;
  onSort: (key: string) => void;
  numeric?: boolean;
}) {
  const active = sort?.key === column;
  return (
    <th
      className={numeric ? "number" : undefined}
      aria-sort={
        active
          ? sort.direction === "asc"
            ? "ascending"
            : "descending"
          : "none"
      }
    >
      <button
        className="sort-header"
        onClick={() => onSort(column)}
        title={`Sort by ${label.toLowerCase()}`}
      >
        {label}
        <span aria-hidden="true">
          {active ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}
        </span>
      </button>
    </th>
  );
}

export function TransactionTable({
  items,
  onEdit,
  onDelete,
  sort: externalSort,
  onSort,
}: {
  items: Entity[];
  onEdit: (r: Entity) => void;
  onDelete?: (r: Entity) => void;
  sort?: TableSort | null;
  onSort?: (key: string) => void;
}) {
  const { accounts, categories } = useApp();
  const [localSort, setLocalSort] = useState<TableSort | null>(null);
  const sort = onSort ? externalSort || null : localSort;
  const changeSort =
    onSort || ((key: string) => setLocalSort(toggleSort(localSort, key)));
  const name = (id: string) =>
    accounts.find((a: Entity) => a.id === id)?.name || "—";
  const category = (id: string) =>
    categories.find((c: Entity) => c.id === id)?.name || "Uncategorized";
  const accountName = (t: Entity) =>
    name(t.type === "Income" ? t.destination_account_id : t.source_account_id);
  const sortedItems =
    !onSort && sort
      ? [...items].sort((a, b) => {
          const value = (t: Entity) =>
            String(
              sort.key === "account"
                ? accountName(t)
                : sort.key === "category"
                  ? category(t.category_id)
                  : t[sort.key] || "",
            ).toLowerCase();
          const comparison =
            sort.key === "amount"
              ? compareAmounts(
                  signedTransactionAmount(a),
                  signedTransactionAmount(b),
                )
              : value(a) < value(b)
                ? -1
                : value(a) > value(b)
                  ? 1
                  : 0;
          return comparison * (sort.direction === "asc" ? 1 : -1);
        })
      : items;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <SortHeader
              label="Transaction"
              column="description"
              sort={sort}
              onSort={changeSort}
            />
            <SortHeader
              label="Date"
              column="date"
              sort={sort}
              onSort={changeSort}
            />
            <SortHeader
              label="Category"
              column="category"
              sort={sort}
              onSort={changeSort}
            />
            <SortHeader
              label="Account"
              column="account"
              sort={sort}
              onSort={changeSort}
            />
            <SortHeader
              label="Amount"
              column="amount"
              sort={sort}
              onSort={changeSort}
              numeric
            />
            <th>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {sortedItems.map((t) => (
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
                    t.type === "Income"
                      ? t.destination_account_id
                      : t.source_account_id,
                  )}
                </span>
                <div className="cell-sub">
                  to {name(t.destination_account_id)}
                </div>
              </td>
              <td
                className={`number amount ${Number(t.amount) < 0 || (t.type === "Expense" && !t.is_refund) ? "negative" : t.type === "Income" || t.is_refund ? "positive" : ""}`}
              >
                {t.type === "Expense" && !t.is_refund
                  ? "−"
                  : t.type === "Transfer" || Number(t.amount) < 0
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
  const { data: importSettings } = useLoad<{ ocr_configured?: boolean }>(
    "/settings",
    {},
    version,
  );
  const [importOpen, setImportOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<TableSort | null>(null);
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
    `/transactions?${query({ ...filters, page, page_size: 25, sort_by: sort?.key, sort_direction: sort?.direction })}`,
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
            <div className="transaction-actions">
              <AddButton onClick={() => setEditor(null)}>
                Add transaction
              </AddButton>
              {importSettings.ocr_configured && (
                <Button variant="secondary" onClick={() => setImportOpen(true)}>
                  <FileUp size={16} />
                  Import Transactions
                </Button>
              )}
            </div>
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
            {["Income", "Expense", "Transfer"].map((t) => (
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
            sort={sort || { key: "date", direction: "desc" }}
            onSort={(key) => {
              setSort(
                toggleSort(sort || { key: "date", direction: "desc" }, key),
              );
              setPage(1);
            }}
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
      {importOpen && <TransactionImport onClose={() => setImportOpen(false)} />}
    </>
  );
}

function CloseAccountDialog({
  account,
  onClose,
  onSaved,
}: {
  account: Entity;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify, refresh } = useApp();
  const [closedOn, setClosedOn] = useState(today());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function closeAccount(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/accounts/${account.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          closed_on: closedOn,
          revision: account.revision,
        }),
      });
      notify("Account closed. History remains available in reports.");
      refresh();
      onSaved();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={`Close ${account.name}`} onClose={onClose}>
      <form onSubmit={closeAccount}>
        <p className="dialog-copy">
          The net balance must be zero, with no activity after the closing date.
          Transactions dated on or before this date remain available, including
          in reports. Use Show archived in Accounts to find closed accounts.
        </p>
        <Field label="Closed on">
          <input
            type="date"
            required
            min={account.opening_date}
            value={closedOn}
            onChange={(e) => setClosedOn(e.target.value)}
          />
        </Field>
        <ErrorBox error={error} />
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" busy={busy}>
            Close account
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function Accounts() {
  const { accounts, version } = useApp();
  const fields = useFields("accounts");
  const [editor, setEditor] = useState<Entity | null | undefined>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [closing, setClosing] = useState<Entity | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [reconcile, setReconcile] = useState<Entity | null>(null);
  const [removed, setRemoved] = useState<Entity | null>(null);
  const [group, setGroup] = useState("owned");
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const valuations = useLoad<ListResult>(
    "/valuations?" + query({ account_id: selectedId, page_size: 500 }),
    { items: [], total: 0, page: 1, page_size: 500 },
    version,
  );
  const selected = accounts.find((a: Entity) => a.id === selectedId);
  const visible = accounts.filter((a: Entity) => showArchived || !a.closed_on);
  const owned = visible.filter(
    (a: Entity) =>
      ["asset", "liability"].includes(a.account_class) &&
      (!typeFilter || a.type === typeFilter) &&
      `${a.name} ${a.account_number || ""} ${a.description || ""}`
        .toLowerCase()
        .includes(search.toLowerCase()),
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
  const shown = visible.filter(
    (a: Entity) =>
      (group === "owned"
        ? ["asset", "liability"].includes(a.account_class)
        : !["asset", "liability"].includes(a.account_class)) &&
      (!typeFilter || a.type === typeFilter) &&
      `${a.name} ${a.account_number || ""} ${a.description || ""}`
        .toLowerCase()
        .includes(search.toLowerCase()),
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
            subtitle={`${selected.type} · ${selected.currency}${selected.account_number ? ` · ${selected.account_number}` : ""}`}
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
                  {!selected.closed_on && (
                    <Button
                      variant="secondary"
                      onClick={() => setClosing(selected)}
                    >
                      <Archive size={16} />
                      Close account
                    </Button>
                  )}
                  <Button onClick={() => setReconcile(selected)}>
                    <Scale size={16} />
                    Reconcile
                  </Button>
                </>
              )
            }
          />
          {selected.closed_on && (
            <p className="notice">
              Closed on {prettyDate(selected.closed_on)}. Historical activity is
              retained in reports.
            </p>
          )}
          {selected.description && (
            <p style={{ whiteSpace: "pre-wrap" }}>{selected.description}</p>
          )}
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
              icon={<Landmark size={19} />}
            />
            <Stat
              label="Total liabilities"
              value={money(liability)}
              icon={<CreditCard size={19} />}
            />
            <Stat
              label="Net worth"
              accent
              value={money(
                decimalSum([
                  asset,
                  liability.startsWith("-")
                    ? liability.slice(1)
                    : "-" + liability,
                ]),
              )}
              icon={<HoardEggIcon />}
            />
          </div>
          <div className="section-header">
            <Button
              variant="secondary"
              aria-pressed={showArchived}
              onClick={() => setShowArchived(!showArchived)}
            >
              <Archive size={16} />
              {showArchived ? "Hide archived" : "Show archived"}
            </Button>
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
            <input
              aria-label="Search accounts by name or account number"
              placeholder="Search name or account number"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              aria-label="Filter by account type"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All account types</option>
              {Array.from(
                new Set<string>(
                  visible
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
                          {!a.closed_on && (
                            <button
                              className="icon-button"
                              title="Close account"
                              aria-label={`Close ${a.name}`}
                              onClick={() => setClosing(a)}
                            >
                              <Archive size={15} />
                            </button>
                          )}
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
                    {a.closed_on
                      ? `Closed on ${prettyDate(a.closed_on)} · `
                      : ""}
                    {a.type}{" "}
                    {a.account_class === "liability" ? "· Amount owed" : ""}
                    {a.account_number ? ` · ${a.account_number}` : ""}
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
      {closing && (
        <CloseAccountDialog
          account={closing}
          onClose={() => setClosing(null)}
          onSaved={() => setSelectedId(null)}
        />
      )}
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
  const [sort, setSort] = useState<TableSort | null>(null);
  const [editor, setEditor] = useState<Entity | null | undefined>();
  const [removed, setRemoved] = useState<Entity | null>(null);
  const sortable = ["valuations", "subscriptions"].includes(resource);
  const { data, loading, error } = useLoad<ListResult>(
    `/${resource}?${query({ page, page_size: 25, sort_by: sortable ? sort?.key : undefined, sort_direction: sortable ? sort?.direction : undefined })}`,
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
                  {columns.map((c) =>
                    sortable ? (
                      <SortHeader
                        key={c.key}
                        label={c.label}
                        column={c.key}
                        sort={sort}
                        onSort={(key) => {
                          setSort(toggleSort(sort, key));
                          setPage(1);
                        }}
                      />
                    ) : (
                      <th key={c.key}>{c.label}</th>
                    ),
                  )}
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
