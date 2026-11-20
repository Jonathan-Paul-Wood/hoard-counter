export type TableSort = { key: string; direction: "asc" | "desc" };

export function toggleSort(sort: TableSort | null, key: string): TableSort {
  return {
    key,
    direction: sort?.key === key && sort.direction === "asc" ? "desc" : "asc",
  };
}

export function compareAmounts(left: string, right: string): number {
  const parts = (value: string) => {
    const [whole, fraction = ""] = value.split(".");
    return { whole, fraction };
  };
  const a = parts(left),
    b = parts(right);
  const scale = Math.max(a.fraction.length, b.fraction.length);
  const integer = (value: ReturnType<typeof parts>) =>
    BigInt(value.whole + value.fraction.padEnd(scale, "0"));
  const x = integer(a),
    y = integer(b);
  return x < y ? -1 : x > y ? 1 : 0;
}

export function signedTransactionAmount(row: Record<string, any>): string {
  const amount = String(row.amount);
  return row.type === "Expense" && !row.is_refund
    ? amount.startsWith("-")
      ? amount.slice(1)
      : `-${amount}`
    : amount;
}
