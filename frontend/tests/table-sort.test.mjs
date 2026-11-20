import test from "node:test";
import assert from "node:assert/strict";
import {
  compareAmounts,
  signedTransactionAmount,
  toggleSort,
} from "../lib/table-sort.ts";

test("amount comparisons preserve decimal precision and signed order", () => {
  assert.equal(
    compareAmounts("9007199254740993.01", "9007199254740993.02"),
    -1,
  );
  assert.equal(compareAmounts("-0.00000002", "-0.00000001"), -1);
  assert.equal(compareAmounts("10", "2"), 1);
  assert.equal(compareAmounts("1.00", "1"), 0);
  assert.equal(
    signedTransactionAmount({ type: "Expense", amount: "20" }),
    "-20",
  );
  assert.equal(
    signedTransactionAmount({ type: "Expense", amount: "20", is_refund: true }),
    "20",
  );
});

test("sort toggles direction and starts new columns ascending", () => {
  const first = toggleSort(null, "date");
  assert.deepEqual(first, { key: "date", direction: "asc" });
  assert.deepEqual(toggleSort(first, "date"), {
    key: "date",
    direction: "desc",
  });
  assert.deepEqual(toggleSort(first, "amount"), {
    key: "amount",
    direction: "asc",
  });
});
