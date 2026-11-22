import { test } from "node:test";
import assert from "node:assert/strict";
import {
  combineTransfer,
  combineExistingTransfer,
} from "../lib/statement-import.ts";

const row = (id, incoming, changes = {}) => ({
  id,
  selected: true,
  sources: [{ filename: `${id}.pdf`, document: 1, page: 1 }],
  transaction: {
    type: incoming ? "Income" : "Expense",
    date: incoming ? "2025-01-31" : "2025-01-01",
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

test("combining mirrors accepts exactly 30 days and keeps outgoing date and both sources", () => {
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
    { date: "2025-02-01" }, // 31 days after the outgoing entry.
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

test("matching an existing outgoing or incoming entry keeps the pending row identity and saved revision", () => {
  for (const existingIncoming of [true, false]) {
    const pending = row("pending", !existingIncoming);
    const saved = row("saved", existingIncoming);
    saved.transaction.description = "Original saved description";
    pending.existing_transfer_candidates = [
      {
        id: saved.id,
        revision: 4,
        transaction: saved.transaction,
        sources: saved.sources,
      },
    ];
    const rows = [pending];
    const original = structuredClone(rows);
    const result = combineExistingTransfer(rows, "pending", "saved", financial);
    assert.equal(result.length, 1);
    assert.equal(result[0].id, "pending");
    assert.equal(result[0].existing_transaction_id, "saved");
    assert.equal(result[0].existing_revision, 4);
    assert.equal(result[0].transaction.type, "Transfer");
    assert.equal(result[0].transaction.date, saved.transaction.date);
    assert.equal(
      result[0].transaction.description,
      "Original saved description",
    );
    assert.equal(result[0].sources.length, 2);
    assert.deepEqual(rows, original); // Undo restores the pending row without changing saved records.
  }
});

test("a saved transaction cannot be used twice and edited mismatches cannot combine", () => {
  const pending = row("pending", true);
  const saved = row("saved", false);
  pending.existing_transfer_candidates = [
    { id: "saved", revision: 1, transaction: saved.transaction, sources: [] },
  ];
  const reserved = { ...row("other", false), existing_transaction_id: "saved" };
  const rows = [pending, reserved];
  assert.equal(
    combineExistingTransfer(rows, "pending", "saved", financial),
    rows,
  );
  pending.transaction.amount = "11";
  const edited = [pending];
  assert.equal(
    combineExistingTransfer(edited, "pending", "saved", financial),
    edited,
  );
});
