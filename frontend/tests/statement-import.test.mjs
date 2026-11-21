import { test } from "node:test";
import assert from "node:assert/strict";
import { combineTransfer } from "../lib/statement-import.ts";

const row = (id, incoming, changes = {}) => ({
  id,
  selected: true,
  sources: [{ filename: `${id}.pdf`, document: 1, page: 1 }],
  transaction: {
    type: incoming ? "Income" : "Expense",
    date: incoming ? "2025-01-04" : "2025-01-01",
    amount: incoming ? "10.00" : "10",
    currency: "USD",
    source_account_id: incoming ? "income" : "checking",
    destination_account_id: incoming ? "savings" : "expense",
    description: "Transfer",
    ...changes,
  },
  transfer_candidates: [incoming ? "a" : "b"],
  duplicate_ids: [],
  errors: [],
  warnings: [],
});
const financial = new Set(["checking", "savings"]);

test("combining mirrors keeps outgoing date and both sources without mutating originals", () => {
  const original = [row("a", false), row("b", true)];
  const snapshot = structuredClone(original);
  const result = combineTransfer(original, "b", "a", financial);
  assert.equal(result.length, 1);
  assert.equal(result[0].transaction.type, "Transfer");
  assert.equal(result[0].transaction.date, "2025-01-01");
  assert.equal(result[0].transaction.source_account_id, "checking");
  assert.equal(result[0].transaction.destination_account_id, "savings");
  assert.equal(result[0].sources.length, 2);
  assert.deepEqual(original, snapshot); // Undo can restore the saved originals.
});

test("edited amounts, same accounts, distant dates and cross-currency pairs cannot combine", () => {
  for (const changes of [
    { amount: "10.01" },
    { destination_account_id: "checking" },
    { date: "2025-01-05" },
    { currency: "EUR" },
  ]) {
    const rows = [row("a", false), row("b", true, changes)];
    assert.equal(combineTransfer(rows, "a", "b", financial), rows);
  }
});

test("exact amounts beyond floating point precision are compared without rounding", () => {
  const rows = [
    row("a", false, { amount: "9007199254740992.01" }),
    row("b", true, { amount: "9007199254740992.02" }),
  ];
  assert.equal(combineTransfer(rows, "a", "b", financial), rows);
});
