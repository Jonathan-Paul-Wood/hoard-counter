import type { StatementRow } from "./generated";

export function combineTransfer(
  rows: StatementRow[],
  leftId: string,
  rightId: string,
  financialIds: Set<string>,
): StatementRow[] {
  const left = rows.find((row) => row.id === leftId);
  const right = rows.find((row) => row.id === rightId);
  if (!left || !right) return rows;
  const outgoing = financialIds.has(left.transaction.source_account_id)
    ? left
    : right;
  const incoming = outgoing === left ? right : left;
  const source = outgoing.transaction;
  const destination = incoming.transaction;
  const amountKey = (value: string) => {
    const [whole, fraction = ""] = value.split(".");
    return `${whole.replace(/^0+(?=\d)/, "")}.${fraction.replace(/0+$/, "")}`;
  };
  const days =
    Math.abs(Date.parse(source.date) - Date.parse(destination.date)) / 86400000;
  if (
    !financialIds.has(source.source_account_id) ||
    !financialIds.has(destination.destination_account_id) ||
    source.source_account_id === destination.destination_account_id ||
    source.currency !== destination.currency ||
    !/^\d+(\.\d+)?$/.test(source.amount) ||
    !/^\d+(\.\d+)?$/.test(destination.amount) ||
    amountKey(source.amount) !== amountKey(destination.amount) ||
    !Number.isFinite(days) ||
    days > 30 ||
    source.type === "Transfer" ||
    destination.type === "Transfer"
  )
    return rows;
  const merged: StatementRow = {
    ...outgoing,
    selected: true,
    transaction: {
      ...source,
      type: "Transfer",
      destination_account_id: destination.destination_account_id,
      destination_amount: destination.amount,
      destination_currency: destination.currency,
      is_refund: false,
      investment_fee: false,
      category_id: null,
      subscription_id: null,
      subscription_date: null,
      notes: [source.notes, destination.notes].filter(Boolean).join("\n"),
      tag_ids: [
        ...new Set([...(source.tag_ids || []), ...(destination.tag_ids || [])]),
      ],
    },
    sources: [...outgoing.sources, ...incoming.sources],
    warnings: [...(outgoing.warnings || []), ...(incoming.warnings || [])],
    duplicate_ids: [
      ...new Set([
        ...(outgoing.duplicate_ids || []),
        ...(incoming.duplicate_ids || []),
      ]),
    ],
    acknowledged_duplicates: [],
    transfer_candidates: [],
    existing_transfer_candidates: [],
    errors: [],
  };
  return rows
    .filter((row) => row.id !== incoming.id)
    .map((row) =>
      row.id === outgoing.id
        ? merged
        : {
            ...row,
            transfer_candidates: row.transfer_candidates?.filter(
              (id) => id !== leftId && id !== rightId,
            ),
          },
    );
}

export function combineExistingTransfer(
  rows: StatementRow[],
  rowId: string,
  existingId: string,
  financialIds: Set<string>,
): StatementRow[] {
  const row = rows.find((item) => item.id === rowId);
  const candidate = row?.existing_transfer_candidates?.find(
    (item) => item.id === existingId,
  );
  if (
    !row ||
    !candidate ||
    rows.some((item) => item.existing_transaction_id === existingId)
  )
    return rows;
  const saved: StatementRow = {
    id: `existing-${candidate.id}`,
    transaction: candidate.transaction,
    sources: candidate.sources,
  };
  const combined = combineTransfer(
    [row, saved],
    row.id,
    saved.id,
    financialIds,
  );
  if (combined.length !== 1) return rows;
  const merged: StatementRow = {
    ...combined[0],
    id: row.id,
    existing_transaction_id: candidate.id,
    existing_revision: candidate.revision,
    transaction: {
      ...combined[0].transaction,
      description: candidate.transaction.description,
      date: candidate.transaction.date,
    },
    duplicate_ids: combined[0].duplicate_ids?.filter(
      (id) => id !== candidate.id,
    ),
  };
  return rows.map((item) =>
    item.id === rowId
      ? merged
      : {
          ...item,
          transfer_candidates: item.transfer_candidates?.filter(
            (id) => id !== rowId,
          ),
        },
  );
}
