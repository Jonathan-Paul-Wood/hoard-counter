"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FileUp, Undo2 } from "lucide-react";
import { api, ApiError, Entity, errorMessage } from "@/lib/api";
import type {
  StatementPreview,
  StatementRow,
  StatementTransaction,
  StatementImportResult,
  PendingReview,
  ImportJob,
  ImportJobFile,
} from "@/lib/generated";
import {
  combineTransfer,
  combineExistingTransfer,
} from "@/lib/statement-import";
import { Button, ErrorBox, Field, Modal, useApp } from "./ui";

const importSteps = [
  "processing_file",
  "extracting_transactions",
  "identifying_accounts",
  "determining_categories",
] as const;

function ImportStepDots({
  file,
  active,
}: {
  file: ImportJobFile;
  active: boolean;
}) {
  const current = importSteps.findIndex((step) => step === file.status);
  return (
    <span className="import-step-dots" aria-hidden="true">
      {importSteps.map((step, index) => {
        const complete =
          file.state !== "queued" &&
          (file.state === "succeeded" ||
            file.status === "completed" ||
            index < current ||
            (index === current &&
              file.state === "running" &&
              file.total !== null &&
              file.completed >= file.total));
        const running =
          !complete && active && file.state === "running" && index === current;
        return (
          <span
            key={step}
            className={`import-step-dot${complete ? " complete" : running ? " running" : ""}`}
          />
        );
      })}
    </span>
  );
}

export function TransactionImport({
  onClose,
  reviewOnly = false,
}: {
  onClose: () => void;
  reviewOnly?: boolean;
}) {
  const { accounts, categories, tags, subscriptions, meta, notify, refresh } =
    useApp();
  const [files, setFiles] = useState<File[]>([]);
  const [preview, setPreview] = useState<StatementPreview | null>(null);
  const [rows, setRowsInternal] = useState<StatementRow[]>([]);
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [dirty, setDirty] = useState(false);
  const baseline = useRef(new Map<string, StatementRow>());
  const hidden = useRef(new Set<string>());
  const saveRequest = useRef<{ body: string; operationId: string } | null>(
    null,
  );
  const loadSequence = useRef(0);
  const saving = useRef(false);
  const setRows: typeof setRowsInternal = (value) => {
    setDirty(true);
    setRowsInternal(value);
  };
  const [reviewing, setReviewing] = useState(reviewOnly);
  const [undo, setUndo] = useState<StatementRow[][]>([]);
  const [busy, setBusy] = useState(false);
  const close = useCallback(() => {
    if (
      !busy &&
      (!dirty ||
        window.confirm(
          "Discard unsaved changes? Saved drafts and background imports will remain.",
        ))
    )
      onClose();
  }, [busy, dirty, onClose]);
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
  async function loadReview(reset = false) {
    if (saving.current && !reset) return;
    const sequence = ++loadSequence.current;
    const result = await api<PendingReview>("/transactions/import/review");
    if (sequence !== loadSequence.current || (saving.current && !reset)) return;
    setJobs(result.jobs);
    setPreview({ batch_id: "pending", rows: result.rows, warnings: [] });
    setRowsInternal((current) => {
      if (reset) {
        baseline.current = new Map(result.rows.map((row) => [row.id, row]));
        hidden.current.clear();
        return result.rows;
      }
      const remote = new Map(result.rows.map((row) => [row.id, row]));
      const retained = current.flatMap((row) => {
        const original = baseline.current.get(row.id);
        if (original && JSON.stringify(original) === JSON.stringify(row)) {
          const updated = remote.get(row.id);
          if (!updated) {
            baseline.current.delete(row.id);
            return [];
          }
          baseline.current.set(row.id, updated);
          return [updated];
        }
        return [row];
      });
      const existing = new Set(retained.map((row) => row.id));
      const added = result.rows.filter(
        (row) => !existing.has(row.id) && !hidden.current.has(row.id),
      );
      added.forEach((row) => baseline.current.set(row.id, row));
      return [...retained, ...added];
    });
  }
  useEffect(() => {
    if (!reviewing) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        if (active) await loadReview();
      } catch (e) {
        if (active) setError(errorMessage(e));
      }
      if (active) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [reviewing]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function analyze(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = new FormData();
      files.forEach((file) => body.append("documents", file));
      const result = await api<{ already_received?: boolean }>(
        "/transactions/import/jobs",
        { method: "POST", body },
      );
      if (result.already_received)
        notify(
          "These files were already received. Existing drafts are available in Review Imports.",
        );
      setReviewing(true);
      await loadReview(true);
      setDirty(false);
      refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function approve() {
    saving.current = true;
    ++loadSequence.current;
    setBusy(true);
    setError("");
    try {
      const deleted = [...baseline.current.values()]
        .filter((row) => !rows.some((current) => current.id === row.id))
        .map((row) => ({ id: row.id, revision: row.revision }));
      const changes = rows.filter(
        (row) =>
          JSON.stringify(row) !== JSON.stringify(baseline.current.get(row.id)),
      );
      if (!changes.length && !deleted.length) {
        setDirty(false);
        return;
      }
      const body = JSON.stringify({ rows: changes, deleted });
      if (!saveRequest.current || saveRequest.current.body !== body)
        saveRequest.current = { body, operationId: crypto.randomUUID() };
      const result = await api<StatementImportResult>(
        "/transactions/import/review/save",
        {
          method: "POST",
          body: JSON.stringify({
            operation_id: saveRequest.current.operationId,
            rows: changes,
            deleted,
          }),
        },
      );
      notify(
        result.total
          ? `Saved drafts and accepted ${result.total} transactions.`
          : "Pending changes saved.",
      );
      saveRequest.current = null;
      await loadReview(true);
      setUndo([]);
      setDirty(false);
      setAcknowledged(false);
      refresh();
    } catch (e) {
      setError(errorMessage(e));
      if (e instanceof ApiError && e.details?.error?.details?.rows) {
        const conflicts = e.details.error.details.rows;
        setRowsInternal((current) =>
          current.map((row) => {
            const conflict = conflicts.find(
              (item: { row_id: string }) => item.row_id === row.id,
            );
            return conflict
              ? { ...row, duplicate_ids: conflict.duplicate_ids }
              : row;
          }),
        );
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }
  async function cancelChecks(id: string) {
    try {
      await api(`/transactions/import/jobs/${id}/cancel`, { method: "POST" });
      await loadReview();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  async function clearJob(id: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/transactions/import/jobs/${id}`, { method: "DELETE" });
      await loadReview();
      refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  function removeDraft(id: string) {
    hidden.current.add(id);
    setRows((current) => current.filter((row) => row.id !== id));
    setUndo([]);
  }
  function merge(left: string, right: string, existing = false) {
    let next = existing
      ? combineExistingTransfer(rows, left, right, financialIds)
      : combineTransfer(rows, left, right, financialIds);
    if (next === rows) {
      setError(
        "These edited rows no longer match a transfer. Review their accounts, currencies, amounts, and dates.",
      );
      return;
    }
    const removed = rows.filter(
      (row) => !next.some((item) => item.id === row.id),
    );
    removed.forEach((row) => hidden.current.add(row.id));
    next = next.map((row) =>
      row.transaction.type === "Transfer" &&
      row.sources.length >
        (rows.find((item) => item.id === row.id)?.sources.length || 0)
        ? {
            ...row,
            combined_ids: [
              ...new Set([
                ...(row.combined_ids || []),
                ...removed.flatMap((item) => [
                  item.id,
                  ...(item.combined_ids || []),
                ]),
              ]),
            ],
          }
        : row,
    );
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
      title={reviewing ? "Review Imports" : "Import Transactions"}
      subtitle={
        reviewing
          ? "Edit drafts, mark accepted transactions, then save. Closing this window does not stop imports."
          : "Upload statements, CSV lists or receipts for background processing."
      }
      onClose={close}
      wide
      className="statement-import-modal"
    >
      <div className="statement-import-body">
        {reviewing &&
          jobs.map((job) => (
            <section
              className="notice"
              key={job.id}
              aria-label="Import progress"
            >
              <strong>
                {job.state === "ack_pending"
                  ? "Results saved"
                  : job.state.replaceAll("_", " ")}
              </strong>
              <span>
                {" "}
                —{" "}
                {job.files.filter((file) => file.state === "succeeded").length}/
                {job.files.length} files completed
              </span>
              {job.files.map((file) => (
                <p className="small import-job-progress" key={file.run_id}>
                  <ImportStepDots
                    file={file}
                    active={["processing", "delayed"].includes(job.state)}
                  />
                  <span>
                    {file.filename}:{" "}
                    {file.state === "queued"
                      ? "Queued"
                      : file.state === "failed"
                        ? "Failed"
                        : file.status.replaceAll("_", " ")}
                    {file.state !== "failed" &&
                      ` — ${file.completed}/${file.total ?? "?"}`}
                    {file.error != null &&
                      typeof file.error === "object" &&
                      "message" in file.error &&
                      ` — ${String(file.error.message)}`}
                  </span>
                </p>
              ))}
              {job.error && <p role="alert">{job.error}</p>}
              {job.warnings?.map((warning, index) => (
                <p key={index}>{warning}</p>
              ))}
              {job.state === "delayed" && (
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => void cancelChecks(job.id)}
                >
                  Cancel checks
                </Button>
              )}
              {["completed", "failed", "cancelled"].includes(job.state) && (
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => void clearJob(job.id)}
                >
                  Clear status
                </Button>
              )}
            </section>
          ))}
        {!reviewing ? (
          <form onSubmit={analyze}>
            <Field
              label="Statements, transaction lists or receipts"
              hint="PDF, CSV, PNG, JPEG, TIFF or WebP. Up to five files and 20 MiB including account/category/rule context; processor limits may be lower."
            >
              <input
                type="file"
                multiple
                required
                disabled={busy}
                accept="text/csv,.csv,application/pdf,image/png,image/jpeg,image/tiff,image/webp,.pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp"
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
              />
            </Field>
            <p className="muted small">
              Files, account details, categories and rules will be sent to OCR
              Processor and its SystemOne model. Original files are discarded
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
              Review dates, amounts, accounts, and categories before accepting.
              Drafts stay pending unless marked Accepted when saved. Deletions
              take effect when saved. Transfer suggestions require explicit
              combination. Matching a saved transaction updates it into a
              transfer when approved.
            </p>
            {preview?.warnings.map((warning, i) => (
              <p className="muted small" key={i}>
                {warning}
              </p>
            ))}
            {!rows.length && (
              <p>
                No pending transactions. Background results will appear here
                when ready.
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
                      <th>Accepted</th>
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
                              aria-label={`Accepted row ${index + 1}`}
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
                            <Button
                              variant="text"
                              onClick={() => removeDraft(row.id)}
                            >
                              Delete draft
                            </Button>
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
              <Button variant="secondary" disabled={busy} onClick={close}>
                Close
              </Button>
              <Button
                busy={busy}
                disabled={
                  !dirty ||
                  (selected.length > 0 &&
                    (!acknowledged || unacknowledgedDuplicates))
                }
                onClick={approve}
              >
                Save changes
                {selected.length ? ` and accept ${selected.length}` : ""}
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
