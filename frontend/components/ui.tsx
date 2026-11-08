"use client";

import { HoardEggIcon } from "./hoard-art";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  AlertCircle,
  ArrowDownLeft,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  Plus,
  X,
  ArrowLeftRight,
} from "lucide-react";
import { api, errorMessage, money } from "@/lib/api";

export const AppContext = createContext<any>(null);
export const useApp = () => useContext(AppContext);
export function useLoad<T = any>(path: string, initial: T, refresh = 0) {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api<T>(path)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, refresh]);
  return { data, setData, loading, error };
}
export function Button({
  children,
  variant = "primary",
  className = "",
  busy = false,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: string;
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      className={`button ${variant} ${className}`}
      disabled={props.disabled || busy}
    >
      {busy && <LoaderCircle className="spin" size={16} />} {children}
    </button>
  );
}
export function Empty({
  title = "Nothing here yet",
  message = "Add your first record to get started.",
  action,
}: {
  title?: string;
  message?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <HoardEggIcon size={28} strokeWidth={1.5} />
      </div>
      <h3>{title}</h3>
      <p>{message}</p>
      {action}
    </div>
  );
}
export function ErrorBox({ error }: { error?: string }) {
  return error ? (
    <div className="error-box" role="alert">
      <AlertCircle size={17} />
      <span>{error}</span>
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={22} className="spin" />
      <span>Gathering your numbers…</span>
    </div>
  );
}
export function Panel({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && (
        <header className="panel-head">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
export function Modal({
  title,
  subtitle,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    document.body.style.overflow = "hidden";
    panel.current
      ?.querySelector<HTMLElement>("input,button,select,textarea")
      ?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        const nodes = panel.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]",
        );
        if (!nodes?.length) return;
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.body.style.overflow = "";
      document.removeEventListener("keydown", handler);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <header>
          <div>
            <h2 id="modal-title">{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
export function Stat({
  label,
  value,
  hint,
  accent = false,
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  accent?: boolean;
  icon?: ReactNode;
}) {
  return (
    <div className={`stat ${accent ? "accent" : ""}`}>
      <div className="stat-label">
        {label}
        {icon}
      </div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}
export function PageHead({
  eyebrow = "YOUR HOARD",
  title,
  subtitle,
  action,
}: {
  eyebrow?: string;
  title: string;
  subtitle: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      {action && <div className="head-actions">{action}</div>}
    </div>
  );
}
export function Pagination({
  page,
  total,
  pageSize = 25,
  onChange,
}: {
  page: number;
  total: number;
  pageSize?: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="pagination">
      <span>
        {total
          ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}`
          : "0 records"}
      </span>
      <div>
        <button
          className="icon-button"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <ChevronLeft size={18} />
        </button>
        <span>Page {page}</span>
        <button
          className="icon-button"
          aria-label="Next page"
          disabled={page * pageSize >= total}
          onClick={() => onChange(page + 1)}
        >
          <ChevronRight size={18} />
        </button>
      </div>
    </div>
  );
}
export function TypeBadge({
  type,
  refund = false,
}: {
  type: string;
  refund?: boolean;
}) {
  const Icon =
    type === "Transfer"
      ? ArrowLeftRight
      : type === "Expense" && !refund
        ? ArrowUpRight
        : ArrowDownLeft;
  return (
    <span className={`type-badge ${(refund ? "refund" : type)?.toLowerCase()}`}>
      <Icon size={13} />
      {refund ? "Refund" : type}
    </span>
  );
}
export function Checkmark() {
  return (
    <span className="checkmark">
      <Check size={14} />
    </span>
  );
}
export function AddButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <Button onClick={onClick}>
      <Plus size={16} />
      {children}
    </Button>
  );
}
export function Field({
  label,
  children,
  hint,
  wide = false,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  wide?: boolean;
}) {
  return (
    <label className={`field ${wide ? "full" : ""}`}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

const palette = [
  "#315b46",
  "#91a78d",
  "#c4b285",
  "#7798a0",
  "#ae8671",
  "#c1c9b7",
  "#80775f",
];
export function SpendingBars({ items }: { items: any[] }) {
  const max = Math.max(
    ...items.map((v) => Number(v.amount ?? v.total ?? v.actual ?? 0)),
    1,
  );
  if (!items.length)
    return (
      <Empty
        title="Every category starts somewhere"
        message="Your spending by category will appear here."
      />
    );
  return (
    <div className="spending-bars">
      {items.slice(0, 8).map((item, index) => (
        <div className="spending-row" key={item.category_id || index}>
          <div>
            <span>
              <i style={{ background: palette[index % palette.length] }} />
              {item.name || "Uncategorized"}
            </span>
            <strong>{money(item.amount ?? item.total ?? item.actual)}</strong>
          </div>
          <div className="bar-track">
            <span
              style={{
                width: `${Math.max(1, (Number(item.amount ?? item.total ?? item.actual) / max) * 100)}%`,
                background: palette[index % palette.length],
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
export function TrendChart({
  items,
  projection = false,
}: {
  items: any[];
  projection?: boolean;
}) {
  if (!items.length)
    return (
      <Empty
        title={projection ? "Your future, in perspective" : "Room to grow"}
        message={
          projection
            ? "Calculate a projection to see the path ahead."
            : "Your monthly cash flow will take shape as you add transactions."
        }
      />
    );
  const values = items.flatMap((v) =>
    projection
      ? [Number(v.total)]
      : [Number(v.income || 0), Number(v.expenses || 0)],
  );
  const max = Math.max(...values.map(Math.abs), 1);
  const width = 700,
    height = 190,
    pad = 20;
  const y = (v: number) => height - pad - (v / max) * (height - 2 * pad);
  const points = items
    .map(
      (item, index) =>
        `${pad + (index * (width - 2 * pad)) / Math.max(items.length - 1, 1)},${y(Number(item.total))}`,
    )
    .join(" ");
  return (
    <div className="chart">
      <svg
        viewBox={`0 0 ${width} ${height + 25}`}
        role="img"
        aria-label={
          projection
            ? "Projected hoard net worth over time"
            : "Monthly income and expenses bar chart"
        }
      >
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <line
              x1={pad}
              y1={y(max * v)}
              x2={width - pad}
              y2={y(max * v)}
              stroke="#e8e8df"
              strokeDasharray="3 5"
            />
          </g>
        ))}
        {projection ? (
          <>
            <polyline
              points={`${pad},${height - pad} ${points} ${width - pad},${height - pad}`}
              fill="#e4ebe2"
              stroke="none"
            />
            <polyline
              points={points}
              fill="none"
              stroke="#315b46"
              strokeWidth="3"
              strokeLinejoin="round"
            />
          </>
        ) : (
          items.map((item, index) => {
            const step = (width - 2 * pad) / items.length,
              bar = Math.min(step / 3, 25),
              x = pad + index * step + step / 2;
            return (
              <g key={index}>
                <rect
                  x={x - bar - 2}
                  y={y(Number(item.income || 0))}
                  width={bar}
                  height={Math.max(
                    0,
                    height - pad - y(Number(item.income || 0)),
                  )}
                  rx="3"
                  fill="#315b46"
                >
                  <title>Income: {money(item.income)}</title>
                </rect>
                <rect
                  x={x + 2}
                  y={y(Number(item.expenses || 0))}
                  width={bar}
                  height={Math.max(
                    0,
                    height - pad - y(Number(item.expenses || 0)),
                  )}
                  rx="3"
                  fill="#b4c4ad"
                >
                  <title>Expenses: {money(item.expenses)}</title>
                </rect>
                <text
                  x={x}
                  y={height + 15}
                  textAnchor="middle"
                  fill="#7b8177"
                  fontSize="12"
                >
                  {(item.month || "").slice(5)}
                </text>
              </g>
            );
          })
        )}
      </svg>
      <div className="chart-legend">
        {projection ? (
          <>
            <span>{prettyShort(items[0]?.date)}</span>
            <strong>{money(items[items.length - 1]?.total)}</strong>
            <span>{prettyShort(items[items.length - 1]?.date)}</span>
          </>
        ) : (
          <>
            <span>
              <i style={{ background: "#315b46" }} />
              Income
            </span>
            <span>
              <i style={{ background: "#b4c4ad" }} />
              Expenses
            </span>
          </>
        )}
      </div>
    </div>
  );
}
function prettyShort(v?: string) {
  return v
    ? new Date(v + "T12:00:00").toLocaleDateString("en-US", {
        month: "short",
        year: "numeric",
      })
    : "";
}
