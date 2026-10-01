import type { EntityMeta } from "./generated";
export type Entity = EntityMeta & { [key: string]: any };
export type ListResult<T = Entity> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};
export type Session = {
  user: { id: string; username: string; role: string } | null;
  csrf_token: string;
  setup_required?: boolean;
};
export class ApiError extends Error {
  status: number;
  details: any;
  constructor(message: string, status: number, details: any) {
    super(message);
    this.status = status;
    this.details = details;
  }
}
let csrfToken = "";
export function setCsrf(token: string) {
  csrfToken = token;
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const method = options.method || "GET";
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (method !== "GET") headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("json") ? await response.json() : null;
  if (!response.ok) {
    const msg =
      payload?.error?.message ||
      payload?.message ||
      (typeof payload?.error === "string" ? payload.error : null) ||
      `Request failed (${response.status}). Please try again.`;
    throw new ApiError(msg, response.status, payload);
  }
  return payload as T;
}
export function query(values: Record<string, unknown>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([k, v]) => {
    if (v !== "" && v !== undefined && v !== null) params.set(k, String(v));
  });
  return params.toString();
}
const precisions: Record<string, number> = {
  USD: 2,
  EUR: 2,
  GBP: 2,
  JPY: 0,
  BTC: 8,
  ETH: 18,
  KRW: 0,
};
export function setCurrencyPrecisions(
  currencies: { code: string; precision: number }[],
) {
  currencies.forEach((currency) => {
    if (
      Number.isInteger(currency.precision) &&
      currency.precision >= 0 &&
      currency.precision <= 18
    )
      precisions[currency.code] = currency.precision;
  });
}
export function money(value: unknown, currency = "USD") {
  if (value === null || value === undefined || value === "") return "—";
  const raw = String(value).trim(),
    match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(raw);
  if (!match) return "—";
  const crypto = currency === "BTC" || currency === "ETH",
    precision = precisions[currency] ?? 2;
  let fraction = (match[3] || "").padEnd(precision, "0"),
    whole = BigInt(match[2]);
  let scaled =
    whole * 10n ** BigInt(precision) +
    BigInt(fraction.slice(0, precision) || "0");
  const discarded = fraction.slice(precision),
    first = Number(discarded[0] || 0);
  if (
    first > 5 ||
    (first === 5 && (/[1-9]/.test(discarded.slice(1)) || scaled % 2n === 1n))
  )
    scaled += 1n;
  const normalized = scaled.toString().padStart(precision + 1, "0");
  const grouped = (
    precision ? normalized.slice(0, -precision) : normalized
  ).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  fraction = precision ? normalized.slice(-precision) : "";
  if (crypto) fraction = fraction.replace(/0+$/, "");
  const amount = `${match[1] && scaled !== 0n ? "−" : ""}${grouped}${fraction ? "." + fraction : ""}`;
  if (crypto) return `${amount} ${currency}`;
  try {
    const symbol =
      new Intl.NumberFormat("en-US", { style: "currency", currency })
        .formatToParts(0)
        .find((part) => part.type === "currency")?.value || currency;
    return `${symbol}${amount}`;
  } catch {
    return `${amount} ${currency}`;
  }
}
export function decimalSum(values: unknown[]): string {
  const parsed = values
    .map((value) => /^(-?)(\d+)(?:\.(\d*))?$/.exec(String(value ?? "0")))
    .filter((value): value is RegExpExecArray => !!value);
  const precision = Math.max(
    0,
    ...parsed.map((value) => (value[3] || "").length),
  );
  let sum = 0n;
  for (const match of parsed) {
    const n =
      BigInt(match[2]) * 10n ** BigInt(precision) +
      BigInt((match[3] || "").padEnd(precision, "0") || "0");
    sum += match[1] ? -n : n;
  }
  const absolute = (sum < 0n ? -sum : sum)
    .toString()
    .padStart(precision + 1, "0");
  return `${sum < 0n ? "-" : ""}${precision ? absolute.slice(0, -precision) + "." + absolute.slice(-precision) : absolute}`;
}
export function decimalSubtract(left: unknown, right: unknown): string {
  const negative = String(right ?? "0");
  return decimalSum([
    left,
    negative.startsWith("-") ? negative.slice(1) : "-" + negative,
  ]);
}
export function percentageToRate(value: string): string {
  const match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(value);
  if (!match) throw new Error("Enter a valid annual percentage.");
  const precision = (match[3] || "").length + 2;
  const scaled = (match[2] + (match[3] || "")).padStart(precision + 1, "0");
  return `${match[1]}${scaled.slice(0, -precision)}.${scaled.slice(-precision)}`;
}
export function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
export function monthNow() {
  return today().slice(0, 7);
}
export function prettyDate(value?: string) {
  return value
    ? new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "—";
}
export function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}
