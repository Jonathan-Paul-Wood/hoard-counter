"use client";

import { useCallback, useState } from "react";
import { FileUp, Undo2 } from "lucide-react";
import { api, ApiError, Entity, errorMessage } from "@/lib/api";
import type {
  StatementPreview,
  StatementRow,
  StatementTransaction,
  StatementImportResult,
} from "@/lib/generated";
import {
  combineTransfer,
  combineExistingTransfer,
} from "@/lib/statement-import";
import { Button, ErrorBox, Field, Modal, useApp } from "./ui";

export function TransactionImport({ onClose }: { onClose: () => void }) {
  const { accounts, categories, tags, subscriptions, meta, notify, refresh } =
    useApp();
  const [files, setFiles] = useState<File[]>([]);
  const [preview, setPreview] = useState<StatementPreview | null>(null);
  const [rows, setRows] = useState<StatementRow[]>([]);
  const [undo, setUndo] = useState<StatementRow[][]>([]);
  const [busy, setBusy] = useState(false);
  const close = useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);
  const [error, setError] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const financialIds = new Set<string>(
    accounts
      .filter((a: Entity) => ["asset", "liability"].includes(a.account_class))
      .map((a: Entity) => a.id),
  );
  const currencies: string[] = (meta?.currencies || []).map(
    (c: { code: string }) => c.code,
  );

  function change(
    id: string,
    field: keyof StatementTransaction,
    value: unknown,
  ) {
    setRows((current) =>
      current.map((row) =>
        row.id === id
          ? {
              ...row,
              transaction: {
                ...row.transaction,
                [field]: value,
                ...(field === "type" && value !== "Expense"
                  ? { is_refund: false, investment_fee: false }
                  : {}),
              },
              errors: [],
            }
          : row,
      ),
    );
  }
  async function analyze(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = new FormData();
      files.forEach((file) => body.append("documents", file));
      const result = await api<StatementPreview>(
        "/transactions/import/preview",
        { method: "POST", body },
      );
      setPreview(result);
      setRows(result.rows);
      setUndo([]);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function approve() {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<StatementImportResult>(
        "/transactions/import/commit",
        {
          method: "POST",
          body: JSON.stringify({
            batch_id: preview.batch_id,
            rows: rows.filter((row) => row.selected),
          }),
        },
      );
      notify(
        result.updated_count
          ? `${result.created_count || 0} ${result.created_count === 1 ? "transaction" : "transactions"} created; ${result.updated_count} existing ${result.updated_count === 1 ? "transaction" : "transactions"} updated.`
          : `${result.total} ${result.total === 1 ? "transaction" : "transactions"} imported.`,
      );
      refresh();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
      if (e instanceof ApiError) {
        const details = e.details?.error?.details;
        if (details?.row_id)
          setRows((current) =>
            current.map((row) =>
              row.id === details.row_id
                ? { ...row, errors: details.errors }
                : row,
            ),
          );
        if (details?.rows)
          setRows((current) =>
            current.map((row) => {
              const conflict = details.rows.find(
                (item: { row_id: string }) => item.row_id === row.id,
              );
              return conflict
                ? {
                    ...row,
                    duplicate_ids: conflict.duplicate_ids,
                    acknowledged_duplicates: [],
                  }
                : row;
            }),
          );
      }
    } finally {
      setBusy(false);
    }
  }
  function merge(left: string, right: string, existing = false) {
    const next = existing
      ? combineExistingTransfer(rows, left, right, financialIds)
      : combineTransfer(rows, left, right, financialIds);
    if (next === rows) {
      setError(
        "These edited rows no longer match a transfer. Review their accounts, currencies, amounts, and dates.",
      );
      return;
    }
    setUndo([...undo, rows]);
    setRows(next);
    setError("");
  }
  const selected = rows.filter((row) => row.selected);
  const updateCount = selected.filter(
    (row) => row.existing_transaction_id,
  ).length;
  const createCount = selected.length - updateCount;
  const unacknowledgedDuplicates = selected.some((row) =>
    row.duplicate_ids?.some((id) => !row.acknowledged_duplicates?.includes(id)),
  );

  return (
    <Modal
      title="Import Transactions"
      subtitle="Extract bank statements, review every row, then approve the import."
      onClose={close}
      wide
      className="statement-import-modal"
    >
      <div className="statement-import-body">
        {!preview ? (
          <form onSubmit={analyze}>
            <Field
              label="Bank statements"
              hint="PDF, PNG, JPEG, TIFF or WebP. Up to five files and 20 MiB including extraction instructions; processor limits may be lower."
            >
              <input
                type="file"
                multiple
                required
                disabled={busy}
                accept="application/pdf,image/png,image/jpeg,image/tiff,image/webp,.pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp"
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
              />
            </Field>
            <p className="muted small">
              Files and your account/category matching information will be sent
              to the configured OCR Processor. Original statements are discarded
              after analysis.
            </p>
            {files.map((file, i) => (
              <p key={i} className="small">
                {file.name}
              </p>
            ))}
            {busy && (
              <p role="status">
                Analyzing statements… This can take several minutes. Keep this
                window open.
              </p>
            )}
            <ErrorBox error={error} />
            <div className="modal-footer">
              <Button
                type="button"
                variant="secondary"
                onClick={onClose}
                disabled={busy}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                busy={busy}
                disabled={!files.length || files.length > 5}
              >
                <FileUp size={16} /> Analyze statements
              </Button>
            </div>
          </form>
        ) : (
          <>
            <p className="notice">
              Review dates, amounts, accounts, and categories before approving.
              Possible duplicates start excluded. Transfer suggestions require
              explicit combination. Matching a saved transaction updates it into
              a transfer when approved.
            </p>
            {preview.warnings.map((warning, i) => (
              <p className="muted small" key={i}>
                {warning}
              </p>
            ))}
            {!rows.length && (
              <p>
                No posted transactions were extracted. Try another statement.
              </p>
            )}
            {!!undo.length && (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  setRows(undo[undo.length - 1]);
                  setUndo(undo.slice(0, -1));
                }}
              >
                <Undo2 size={15} /> Undo combine
              </Button>
            )}
            <fieldset disabled={busy} className="import-fieldset">
              <div className="statement-preview-scroll">
                <table className="statement-preview">
                  <thead>
                    <tr>
                      <th>Include</th>
                      <th>Date / description</th>
                      <th>Type / accounts</th>
                      <th>Amounts / currencies</th>
                      <th>Category / tags / notes</th>
                      <th>Source / review</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, index) => {
                      const t = row.transaction;
                      const input = (
                        field: keyof StatementTransaction,
                        label: string,
                        type = "text",
                      ) => (
                        <label className="field">
                          <span>{label}</span>
                          <input
                            aria-label={`${label} row ${index + 1}`}
                            type={type}
                            value={String(t[field] ?? "")}
                            onChange={(e) =>
                              change(row.id, field, e.target.value)
                            }
                          />
                        </label>
                      );
                      const accountSelect = (
                        field: "source_account_id" | "destination_account_id",
                        label: string,
                      ) => (
                        <label className="field">
                          <span>{label}</span>
                          <select
                            aria-label={`${label} row ${index + 1}`}
                            value={t[field]}
                            onChange={(e) =>
                              change(row.id, field, e.target.value)
                            }
                          >
                            <option value="">Choose account…</option>
                            {accounts.map((a: Entity) => (
                              <option key={a.id} value={a.id}>
                                {a.name} ({a.currency})
                                {a.closed_on ? " — archived" : ""}
                              </option>
                            ))}
                          </select>
                        </label>
                      );
                      return (
                        <tr
                          key={row.id}
                          className={
                            row.errors?.length ? "import-row-error" : ""
                          }
                        >
                          <td>
                            <input
                              type="checkbox"
                              aria-label={`Include row ${index + 1}`}
                              checked={!!row.selected}
                              onChange={(e) =>
                                setRows(
                                  rows.map((r) =>
                                    r.id === row.id
                                      ? { ...r, selected: e.target.checked }
                                      : r,
                                  ),
                                )
                              }
                            />
                          </td>
                          <td>
                            {input("date", "Date", "date")}
                            {input("description", "Description")}
                          </td>
                          <td>
                            <label className="field">
                              <span>Type</span>
                              <select
                                aria-label={`Type row ${index + 1}`}
                                value={t.type}
                                onChange={(e) =>
                                  change(row.id, "type", e.target.value)
                                }
                              >
                                <option value="">Choose type…</option>
                                {["Income", "Expense", "Transfer"].map((v) => (
                                  <option key={v}>{v}</option>
                                ))}
                              </select>
                            </label>
                            {accountSelect("source_account_id", "Source")}
                            {accountSelect(
                              "destination_account_id",
                              "Destination",
                            )}
                            {t.type === "Expense" && (
                              <label className="checkbox-line">
                                <input
                                  type="checkbox"
                                  checked={!!t.is_refund}
                                  onChange={(e) =>
                                    change(
                                      row.id,
                                      "is_refund",
                                      e.target.checked,
                                    )
                                  }
                                />{" "}
                                Refund
                              </label>
                            )}
                          </td>
                          <td>
                            {input("amount", "Amount")}
                            <label className="field">
                              <span>Currency</span>
                              <select
                                aria-label={`Currency row ${index + 1}`}
                                value={t.currency}
                                onChange={(e) =>
                                  change(row.id, "currency", e.target.value)
                                }
                              >
                                <option value="">Choose currency…</option>
                                {currencies.map((v) => (
                                  <option key={v}>{v}</option>
                                ))}
                              </select>
                            </label>
                            {t.type === "Transfer" && (
                              <>
                                {input(
                                  "destination_amount",
                                  "Destination amount",
                                )}
                                <label className="field">
                                  <span>Destination currency</span>
                                  <select
                                    value={t.destination_currency || ""}
                                    onChange={(e) =>
                                      change(
                                        row.id,
                                        "destination_currency",
                                        e.target.value,
                                      )
                                    }
                                  >
                                    <option value="">Account currency</option>
                                    {currencies.map((v) => (
                                      <option key={v}>{v}</option>
                                    ))}
                                  </select>
                                </label>
                              </>
                            )}
                          </td>
                          <td>
                            <label className="field">
                              <span>Category</span>
                              <select
                                aria-label={`Category row ${index + 1}`}
                                value={t.category_id || ""}
                                onChange={(e) =>
                                  change(
                                    row.id,
                                    "category_id",
                                    e.target.value || null,
                                  )
                                }
                              >
                                <option value="">Uncategorized</option>
                                {categories.map((c: Entity) => (
                                  <option key={c.id} value={c.id}>
                                    {c.name}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="field">
                              <span>Tags</span>
                              <select
                                multiple
                                aria-label={`Tags row ${index + 1}`}
                                value={t.tag_ids || []}
                                onChange={(e) =>
                                  change(
                                    row.id,
                                    "tag_ids",
                                    Array.from(
                                      e.target.selectedOptions,
                                      (o) => o.value,
                                    ),
                                  )
                                }
                              >
                                {tags.map((tag: Entity) => (
                                  <option key={tag.id} value={tag.id}>
                                    {tag.name}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="field">
                              <span>Notes</span>
                              <textarea
                                rows={2}
                                maxLength={10000}
                                value={t.notes || ""}
                                onChange={(e) =>
                                  change(row.id, "notes", e.target.value)
                                }
                              />
                            </label>
                            {!!subscriptions.length && (
                              <label className="field">
                                <span>Subscription</span>
                                <select
                                  aria-label={`Subscription row ${index + 1}`}
                                  value={t.subscription_id || ""}
                                  onChange={(e) => {
                                    setRows((current) =>
                                      current.map((item) =>
                                        item.id === row.id
                                          ? {
                                              ...item,
                                              errors: [],
                                              transaction: {
                                                ...item.transaction,
                                                subscription_id:
                                                  e.target.value || null,
                                                subscription_date: null,
                                              },
                                            }
                                          : item,
                                      ),
                                    );
                                  }}
                                >
                                  <option value="">No subscription</option>
                                  {subscriptions.map((subscription: Entity) => (
                                    <option
                                      key={subscription.id}
                                      value={subscription.id}
                                    >
                                      {subscription.name}
                                    </option>
                                  ))}
                                </select>
                              </label>
                            )}
                            {t.subscription_id &&
                              input(
                                "subscription_date",
                                "Subscription occurrence",
                                "date",
                              )}
                          </td>
                          <td>
                            {row.sources.map((source, i) => (
                              <p className="small" key={i}>
                                {source.filename}, page {source.page}
                              </p>
                            ))}
                            {row.existing_transaction_id && (
                              <p className="notice small">
                                Approval will update the matched saved
                                transaction into a transfer instead of creating
                                a new entry. Use Undo combine to cancel this
                                match.
                              </p>
                            )}
                            {row.warnings?.map((warning, i) => (
                              <p className="small" key={i}>
                                {warning}
                              </p>
                            ))}
                            {row.errors?.map((message, i) => (
                              <p
                                role="alert"
                                className="small import-error"
                                key={i}
                              >
                                {message}
                              </p>
                            ))}
                            {!!row.duplicate_ids?.length && (
                              <label className="checkbox-line small">
                                <input
                                  type="checkbox"
                                  checked={row.duplicate_ids.every((id) =>
                                    row.acknowledged_duplicates?.includes(id),
                                  )}
                                  onChange={(e) =>
                                    setRows(
                                      rows.map((r) =>
                                        r.id === row.id
                                          ? {
                                              ...r,
                                              acknowledged_duplicates: e.target
                                                .checked
                                                ? r.duplicate_ids
                                                : [],
                                            }
                                          : r,
                                      ),
                                    )
                                  }
                                />{" "}
                                Import despite possible duplicate
                              </label>
                            )}
                            {row.transfer_candidates?.map((id) => {
                              const candidate = rows.find((r) => r.id === id);
                              return (
                                candidate && (
                                  <Button
                                    key={id}
                                    variant="text"
                                    onClick={() => merge(row.id, id)}
                                  >
                                    Combine with row{" "}
                                    {rows.indexOf(candidate) + 1}
                                  </Button>
                                )
                              );
                            })}
                            {row.existing_transfer_candidates?.map(
                              (candidate) => {
                                const saved = candidate.transaction;
                                const ownId = financialIds.has(
                                  saved.source_account_id,
                                )
                                  ? saved.source_account_id
                                  : saved.destination_account_id;
                                const accountName =
                                  accounts.find(
                                    (account: Entity) => account.id === ownId,
                                  )?.name || "Account";
                                return (
                                  <div key={candidate.id}>
                                    <p className="small">
                                      Saved: {saved.description} — {accountName}
                                      , {saved.date}, {saved.amount}{" "}
                                      {saved.currency}
                                    </p>
                                    <Button
                                      variant="text"
                                      disabled={rows.some(
                                        (item) =>
                                          item.existing_transaction_id ===
                                          candidate.id,
                                      )}
                                      onClick={() =>
                                        merge(row.id, candidate.id, true)
                                      }
                                    >
                                      Match saved transaction
                                    </Button>
                                  </div>
                                );
                              },
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </fieldset>
            {!!rows.length && (
              <label className="checkbox-line">
                <input
                  type="checkbox"
                  checked={acknowledged}
                  disabled={busy}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                />{" "}
                I have reviewed the selected transactions.
              </label>
            )}
            <ErrorBox error={error} />
            <div className="modal-footer">
              <Button variant="secondary" disabled={busy} onClick={onClose}>
                Cancel
              </Button>
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  setPreview(null);
                  setRows([]);
                  setAcknowledged(false);
                }}
              >
                Choose other files
              </Button>
              <Button
                busy={busy}
                disabled={
                  !selected.length || !acknowledged || unacknowledgedDuplicates
                }
                onClick={approve}
              >
                {updateCount
                  ? createCount
                    ? `Create ${createCount} and update ${updateCount} transactions`
                    : `Update ${updateCount} ${updateCount === 1 ? "transaction" : "transactions"}`
                  : `Create ${selected.length} ${selected.length === 1 ? "transaction" : "transactions"}`}
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
