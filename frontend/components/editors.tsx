"use client";

import { useState, useEffect } from "react";
import {
  Plus,
  Trash2,
  Paperclip,
  FileText,
  Download,
  Play,
} from "lucide-react";
import { api, Entity, errorMessage, today, money } from "@/lib/api";
import { Button, ErrorBox, Field, Modal, useApp } from "./ui";

export type FieldSpec = {
  name: string;
  label: string;
  type?: string;
  required?: boolean;
  options?: { value: string; label: string }[];
  hint?: string;
  wide?: boolean;
  default?: any;
  min?: string;
  step?: string;
};
export function RecordEditor({
  resource,
  title,
  fields,
  record,
  onClose,
  onSaved,
}: {
  resource: string;
  title: string;
  fields: FieldSpec[];
  record?: Entity;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const { notify, refresh, accounts } = useApp();
  const [values, setValues] = useState<Record<string, any>>(() =>
    Object.fromEntries(
      fields.map((f) => [
        f.name,
        record?.[f.name] ?? f.default ?? (f.type === "checkbox" ? false : ""),
      ]),
    ),
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  function setField(name: string, value: any) {
    setValues((current) => {
      const next = { ...current, [name]: value };
      if (resource === "accounts" && name === "type")
        next.account_class = [
          "Credit Card",
          "Loan",
          "Mortgage",
          "Debt",
        ].includes(value)
          ? "liability"
          : value === "Income Source"
            ? "income"
            : value === "Expense Payee"
              ? "expense"
              : value === "Equity"
                ? "equity"
                : "asset";
      if (resource === "currencies" && name === "code")
        next.code = String(value).toUpperCase();
      if (resource === "subscriptions" && name === "source_account_id")
        next.currency =
          accounts.find((a: Entity) => a.id === value)?.currency ||
          next.currency;
      return next;
    });
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body: any = { ...values };
      for (const field of fields) {
        if (field.type === "integer")
          body[field.name] = Number(body[field.name]);
        if (field.name.endsWith("_id") && body[field.name] === "")
          body[field.name] = null;
        if (field.type === "password" && !body[field.name])
          delete body[field.name];
      }
      if (record) body.revision = record.revision;
      await api(`/${resource}${record ? `/${record.id}` : ""}`, {
        method: record ? "PATCH" : "POST",
        body: JSON.stringify(body),
      });
      notify(`${title} ${record ? "updated" : "created"}.`);
      refresh();
      onSaved?.();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
      notify(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`${record ? "Edit" : "New"} ${title.toLowerCase()}`}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <div className="form-grid">
          {fields.map((f) => (
            <Field key={f.name} label={f.label} hint={f.hint} wide={f.wide}>
              {f.type === "select" ? (
                <select
                  value={values[f.name]}
                  onChange={(e) => setField(f.name, e.target.value)}
                  required={f.required}
                >
                  <option value="">Select…</option>
                  {f.options?.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              ) : f.type === "textarea" ? (
                <textarea
                  value={values[f.name]}
                  onChange={(e) => setField(f.name, e.target.value)}
                  required={f.required}
                  rows={3}
                />
              ) : f.type === "checkbox" ? (
                <span className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={values[f.name]}
                    onChange={(e) =>
                      setValues({ ...values, [f.name]: e.target.checked })
                    }
                  />
                  Enabled
                </span>
              ) : (
                <input
                  type={
                    f.type === "integer"
                      ? "number"
                      : f.type === "decimal"
                        ? "text"
                        : f.type || "text"
                  }
                  inputMode={f.type === "decimal" ? "decimal" : undefined}
                  value={values[f.name]}
                  onChange={(e) => setField(f.name, e.target.value)}
                  required={f.required}
                  min={f.min}
                  step={f.step}
                  autoComplete={f.type === "password" ? "new-password" : "off"}
                />
              )}
            </Field>
          ))}
        </div>
        <ErrorBox error={error} />
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" busy={busy}>
            Save {title.toLowerCase()}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function TransactionEditor({
  record,
  onClose,
  preset,
}: {
  record?: Entity;
  onClose: () => void;
  preset?: Partial<Entity>;
}) {
  const { accounts, categories, tags, subscriptions, notify, refresh } =
    useApp();
  const [values, setValues] = useState<any>({
    type: "Expense",
    source_account_id: "",
    destination_account_id: "",
    date: today(),
    amount: "",
    destination_amount: "",
    description: "",
    category_id: "",
    tag_ids: [],
    notes: "",
    subscription_id: "",
    subscription_date: "",
    is_refund: false,
    investment_fee: false,
    fee_amount: "",
    ...preset,
    ...record,
  });
  const [files, setFiles] = useState<{ file: File; kind: string }[]>([]);
  const [attachments, setAttachments] = useState<any[]>(
    record?.attachments || [],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [savedId, setSavedId] = useState<string | null>(null);
  const [completedUploads, setCompletedUploads] = useState<number[]>([]);
  useEffect(() => {
    if (!record?.id) return;
    let active = true;
    api(`/attachments?transaction_id=${record.id}`)
      .then((r) => {
        if (active) setAttachments(r.items || []);
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      });
    return () => {
      active = false;
    };
  }, [record?.id]);
  const put = (name: string, value: any) =>
    setValues((v: any) => ({ ...v, [name]: value }));
  const source = accounts.find(
      (a: Entity) => a.id === values.source_account_id,
    ),
    dest = accounts.find((a: Entity) => a.id === values.destination_account_id);
  const sourceOptions = accounts.filter((a: Entity) =>
    values.type === "Income" || values.type === "Return"
      ? a.account_class === "income"
      : values.type === "Expense" && values.is_refund
        ? a.account_class === "expense"
        : values.type === "Return"
          ? a.account_class === "asset"
          : ["asset", "liability"].includes(a.account_class),
  );
  const destOptions = accounts.filter((a: Entity) =>
    values.type === "Expense" && !values.is_refund
      ? a.account_class === "expense"
      : values.type === "Return"
        ? a.account_class === "asset"
        : ["asset", "liability"].includes(a.account_class),
  );
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = { ...values };
      for (const key of [
        "id",
        "last_modified",
        "deleted",
        "attachments",
        "usd_amount",
        "fx_rate",
        "destination_fx_rate",
        "postings",
        "source_account",
        "destination_account",
      ])
        delete body[key];
      ["category_id", "subscription_id", "subscription_date"].forEach((k) => {
        if (!body[k]) body[k] = null;
      });
      body.destination_amount =
        source?.currency === dest?.currency
          ? body.amount
          : body.destination_amount || null;
      delete body.fee_amount;
      if (record) body.revision = record.revision;
      let id = savedId;
      if (!id) {
        const saved = await api(
          `/transactions${record ? `/${record.id}` : ""}`,
          { method: record ? "PATCH" : "POST", body: JSON.stringify(body) },
        );
        id = saved.id || saved.item?.id;
        if (!id)
          throw new Error(
            "The server saved the transaction without returning its identifier. Refresh before retrying.",
          );
        setSavedId(id);
      }
      for (let index = 0; index < files.length; index++) {
        if (completedUploads.includes(index)) continue;
        const item = files[index];
        const data = new FormData();
        data.append("file", item.file);
        data.append("kind", item.kind);
        data.append("transaction_id", id!);
        await api("/attachments", { method: "POST", body: data });
        setCompletedUploads((current) => [...current, index]);
      }
      notify(`Transaction ${record ? "updated" : "recorded"}.`);
      refresh();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
      notify(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  }
  async function removeAttachment(attachment: any) {
    try {
      await api(`/attachments/${attachment.id}`, {
        method: "DELETE",
        body: JSON.stringify({ revision: attachment.revision }),
      });
      setAttachments(attachments.filter((a) => a.id !== attachment.id));
      notify("Attachment removed.");
      refresh();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal
      title={record ? "Edit transaction" : "Add a transaction"}
      subtitle="A clear record of money moving from one place to another."
      onClose={onClose}
      wide
    >
      <form onSubmit={submit}>
        <fieldset disabled={!!savedId || busy} className="plain-fieldset">
          <div className="segmented">
            {["Expense", "Income", "Transfer", "Return"].map((type) => (
              <button
                key={type}
                type="button"
                className={values.type === type ? "active" : ""}
                onClick={() => {
                  put("type", type);
                  put("source_account_id", "");
                  put("destination_account_id", "");
                }}
              >
                {type}
              </button>
            ))}
          </div>
          <div className="form-grid">
            <Field label="Description" wide>
              <input
                required
                placeholder="What was this for?"
                value={values.description}
                onChange={(e) => put("description", e.target.value)}
              />
            </Field>
            <Field label="From account">
              <select
                required
                value={values.source_account_id}
                onChange={(e) => put("source_account_id", e.target.value)}
              >
                <option value="">Select source…</option>
                {sourceOptions.map((a: Entity) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.currency}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="To account">
              <select
                required
                value={values.destination_account_id}
                onChange={(e) => put("destination_account_id", e.target.value)}
              >
                <option value="">Select destination…</option>
                {destOptions.map((a: Entity) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.currency}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={`Amount${source ? ` (${source.currency})` : ""}`}>
              <input
                required
                inputMode="decimal"
                placeholder="0.00"
                value={values.amount}
                onChange={(e) => put("amount", e.target.value)}
              />
            </Field>
            <Field label="Date">
              <input
                required
                type="date"
                value={values.date}
                onChange={(e) => put("date", e.target.value)}
              />
            </Field>
            {source && dest && source.currency !== dest.currency && (
              <Field
                label={`Received amount (${dest.currency})`}
                hint="Leave blank to calculate using the dated exchange rate."
              >
                <input
                  inputMode="decimal"
                  value={values.destination_amount}
                  onChange={(e) => put("destination_amount", e.target.value)}
                />
              </Field>
            )}
            <Field label="Category">
              <select
                value={values.category_id || ""}
                onChange={(e) => put("category_id", e.target.value)}
              >
                <option value="">Uncategorized</option>
                {categories.map((c: Entity) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Subscription">
              <select
                value={values.subscription_id || ""}
                onChange={(e) => put("subscription_id", e.target.value)}
              >
                <option value="">Match automatically</option>
                {subscriptions.map((s: Entity) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>
            {values.subscription_id && (
              <Field label="Subscription occurrence date">
                <input
                  type="date"
                  value={values.subscription_date || ""}
                  onChange={(e) => put("subscription_date", e.target.value)}
                />
              </Field>
            )}
            <Field label="Tags" wide>
              <div className="tag-picker">
                {tags.length ? (
                  tags.map((tag: Entity) => (
                    <label
                      key={tag.id}
                      className={
                        (values.tag_ids || []).includes(tag.id)
                          ? "selected"
                          : ""
                      }
                    >
                      <input
                        type="checkbox"
                        checked={(values.tag_ids || []).includes(tag.id)}
                        onChange={(e) =>
                          put(
                            "tag_ids",
                            e.target.checked
                              ? [...(values.tag_ids || []), tag.id]
                              : (values.tag_ids || []).filter(
                                  (id: string) => id !== tag.id,
                                ),
                          )
                        }
                      />
                      {tag.name}
                    </label>
                  ))
                ) : (
                  <span className="muted">
                    Create tags in Settings to organize transactions.
                  </span>
                )}
              </div>
            </Field>
            <Field label="Notes" wide>
              <textarea
                rows={3}
                placeholder="Anything you’d like to remember…"
                value={values.notes || ""}
                onChange={(e) => put("notes", e.target.value)}
              />
            </Field>
            {values.type === "Expense" && (
              <div className="full checkbox-group">
                <label>
                  <input
                    type="checkbox"
                    checked={values.is_refund}
                    onChange={(e) => {
                      put("is_refund", e.target.checked);
                      put("source_account_id", "");
                      put("destination_account_id", "");
                    }}
                  />
                  Purchase refund
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={values.investment_fee}
                    onChange={(e) => put("investment_fee", e.target.checked)}
                  />
                  Investment fee
                </label>
              </div>
            )}
            {values.type === "Transfer" && (
              <p className="muted small full">
                Record transfer fees as a separate expense so they appear in
                spending reports.
              </p>
            )}
          </div>
        </fieldset>
        <div className="attachment-section">
          <h3>
            <Paperclip size={16} />
            Receipts & item images
          </h3>
          {attachments.map((a) => (
            <div className="attachment-row" key={a.id}>
              <FileText size={16} />
              <a
                href={`/api/v1/attachments/${a.id}`}
                target="_blank"
                rel="noreferrer"
              >
                {a.original_name || a.filename || a.name || "Attachment"}
              </a>
              <button
                type="button"
                className="icon-button"
                aria-label="Remove attachment"
                onClick={() => removeAttachment(a)}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          {files.map((item, index) => (
            <div className="attachment-row" key={index}>
              <FileText size={16} />
              <span>{item.file.name}</span>
              <select
                value={item.kind}
                onChange={(e) =>
                  setFiles(
                    files.map((f, i) =>
                      i === index ? { ...f, kind: e.target.value } : f,
                    ),
                  )
                }
              >
                <option value="receipt">Receipt</option>
                <option value="item">Item image</option>
              </select>
              {!savedId && (
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => setFiles(files.filter((_, i) => i !== index))}
                  aria-label="Remove pending file"
                >
                  <Trash2 size={15} />
                </button>
              )}
            </div>
          ))}
          <label className="upload-zone">
            <Paperclip size={20} />
            <span>
              <strong>Choose files</strong> to attach to this transaction
            </span>
            <small>PDF, JPEG, PNG or WebP</small>
            <input
              type="file"
              accept="application/pdf,image/jpeg,image/png,image/webp"
              multiple
              disabled={!!savedId}
              onChange={(e) => {
                setFiles([
                  ...files,
                  ...Array.from(e.target.files || []).map((file) => ({
                    file,
                    kind: "receipt",
                  })),
                ]);
                e.target.value = "";
              }}
            />
          </label>
        </div>
        {savedId && (
          <p className="notice">
            The transaction is saved. Retry to finish uploading the remaining
            attachments.
          </p>
        )}
        <ErrorBox error={error} />
        <div className="modal-footer">
          <span className="muted footer-note">
            Every transaction keeps the ledger balanced.
          </span>
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" busy={busy}>
            {savedId ? "Retry uploads" : "Save transaction"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function RuleEditor({
  record,
  onClose,
}: {
  record?: Entity;
  onClose: () => void;
}) {
  const { accounts, categories, tags, subscriptions, meta, notify, refresh } =
    useApp();
  const [name, setName] = useState(record?.name || "");
  const [priority, setPriority] = useState(record?.priority ?? 10);
  const [enabled, setEnabled] = useState(record?.enabled ?? true);
  const [conditions, setConditions] = useState<any[]>(
    record?.conditions || [
      { field: "description", operator: "contains", value: "" },
    ],
  );
  const [actions, setActions] = useState<any>(record?.actions || {});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const conditionFields = [
    "description",
    "type",
    "source_account_id",
    "destination_account_id",
    "currency",
    "amount",
    "category_id",
    "tag_ids",
  ];
  const fieldNames: Record<string, string> = {
    description: "Description",
    type: "Transaction type",
    source_account_id: "Source account",
    destination_account_id: "Destination account",
    currency: "Currency",
    amount: "Amount",
    category_id: "Category",
    tag_ids: "Tag",
  };
  function choices(field: string) {
    if (field === "source_account_id" || field === "destination_account_id")
      return accounts.map((a: Entity) => ({ value: a.id, label: a.name }));
    if (field === "category_id")
      return categories.map((a: Entity) => ({ value: a.id, label: a.name }));
    if (field === "tag_ids")
      return tags.map((a: Entity) => ({ value: a.id, label: a.name }));
    if (field === "type")
      return ["Income", "Expense", "Transfer", "Return"].map((value) => ({
        value,
        label: value,
      }));
    if (field === "currency")
      return (meta?.currencies || []).map((a: any) => ({
        value: a.code,
        label: a.code,
      }));
    return null;
  }
  function optionalAction(field: string, value: string) {
    setActions((previous: any) => {
      const next = { ...previous };
      if (value) next[field] = value;
      else delete next[field];
      return next;
    });
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/rules${record ? `/${record.id}` : ""}`, {
        method: record ? "PATCH" : "POST",
        body: JSON.stringify({
          name,
          priority,
          enabled,
          conditions,
          actions,
          revision: record?.revision,
        }),
      });
      notify("Rule saved.");
      refresh();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={record ? "Edit rule" : "Create a rule"}
      subtitle="When every condition matches, apply these actions in priority order."
      onClose={onClose}
      wide
    >
      <form onSubmit={save}>
        <div className="form-grid">
          <Field label="Rule name">
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="Priority" hint="Lower numbers run first.">
            <input
              type="number"
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}
            />
          </Field>
        </div>
        <h3 className="form-section-title">When all of these match</h3>
        <div className="rule-conditions">
          {conditions.map((c, i) => (
            <div className="rule-condition" key={i}>
              <select
                aria-label="Condition field"
                value={c.field}
                onChange={(e) =>
                  setConditions(
                    conditions.map((v, j) =>
                      i === j
                        ? {
                            ...v,
                            field: e.target.value,
                            operator:
                              e.target.value === "tag_ids" ||
                              e.target.value === "description"
                                ? "contains"
                                : "eq",
                            value: "",
                          }
                        : v,
                    ),
                  )
                }
              >
                {conditionFields.map((v) => (
                  <option key={v} value={v}>
                    {fieldNames[v] || v}
                  </option>
                ))}
              </select>
              <select
                disabled={c.field === "tag_ids"}
                aria-label="Condition operator"
                value={c.operator}
                onChange={(e) =>
                  setConditions(
                    conditions.map((v, j) =>
                      i === j ? { ...v, operator: e.target.value } : v,
                    ),
                  )
                }
              >
                {[
                  ["eq", "equals"],
                  ["neq", "does not equal"],
                  ["contains", "contains"],
                  ["gte", "at least"],
                  ["lte", "at most"],
                ].map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
              <ConditionValue
                options={choices(c.field)}
                value={c.value}
                onChange={(value) =>
                  setConditions(
                    conditions.map((v, j) => (i === j ? { ...v, value } : v)),
                  )
                }
              />
              <button
                type="button"
                className="icon-button"
                disabled={conditions.length === 1}
                aria-label="Remove condition"
                onClick={() =>
                  setConditions(conditions.filter((_, j) => i !== j))
                }
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
        <Button
          type="button"
          variant="text"
          onClick={() =>
            setConditions([
              ...conditions,
              { field: "description", operator: "contains", value: "" },
            ])
          }
        >
          <Plus size={15} />
          Add condition
        </Button>
        <h3 className="form-section-title">Then make these changes</h3>
        <div className="form-grid">
          <Field label="Set category">
            <select
              value={actions.category_id || ""}
              onChange={(e) => optionalAction("category_id", e.target.value)}
            >
              <option value="">Leave unchanged</option>
              {categories.map((c: Entity) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Link subscription">
            <select
              value={actions.subscription_id || ""}
              onChange={(e) =>
                optionalAction("subscription_id", e.target.value)
              }
            >
              <option value="">Leave unchanged</option>
              {subscriptions.map((s: Entity) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Set notes" wide>
            <textarea
              value={actions.notes ?? ""}
              onChange={(e) =>
                setActions({ ...actions, notes: e.target.value })
              }
            />
          </Field>
          <Field label="Set tags" wide>
            <div className="tag-picker">
              {tags.map((t: Entity) => (
                <label
                  key={t.id}
                  className={
                    (actions.tag_ids || []).includes(t.id) ? "selected" : ""
                  }
                >
                  <input
                    type="checkbox"
                    checked={(actions.tag_ids || []).includes(t.id)}
                    onChange={(e) =>
                      setActions({
                        ...actions,
                        tag_ids: e.target.checked
                          ? [...(actions.tag_ids || []), t.id]
                          : (actions.tag_ids || []).filter(
                              (id: string) => id !== t.id,
                            ),
                      })
                    }
                  />
                  {t.name}
                </label>
              ))}
            </div>
          </Field>
          <label className="checkbox-line">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            Enable this rule
          </label>
        </div>
        <ErrorBox error={error} />
        <div className="modal-footer">
          <Button variant="secondary" type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button busy={busy}>Save rule</Button>
        </div>
      </form>
    </Modal>
  );
}

function ConditionValue({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[] | null;
  value: string;
  onChange: (value: string) => void;
}) {
  return options ? (
    <select
      required
      aria-label="Condition value"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">Select…</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ) : (
    <input
      required
      aria-label="Condition value"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function ReconcileEditor({
  account,
  onClose,
}: {
  account: Entity;
  onClose: () => void;
}) {
  const { notify, refresh } = useApp();
  const [balance, setBalance] = useState(account.balance);
  const [date, setDate] = useState(today());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api(`/accounts/${account.id}/reconcile`, {
        method: "POST",
        body: JSON.stringify({ balance, date, revision: account.revision }),
      });
      notify("Balance adjustment recorded.");
      refresh();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`Reconcile ${account.name}`}
      subtitle="This creates a balanced adjustment with a permanent audit record."
      onClose={onClose}
    >
      <form onSubmit={save}>
        <div className="form-grid">
          <Field
            label={`Actual balance (${account.currency})`}
            hint={
              account.account_class === "liability"
                ? "Enter the positive amount owed."
                : undefined
            }
          >
            <input
              required
              inputMode="decimal"
              value={balance}
              onChange={(e) => setBalance(e.target.value)}
            />
          </Field>
          <Field label="As of">
            <input
              required
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </Field>
        </div>
        <ErrorBox error={error} />
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button busy={busy}>Record adjustment</Button>
        </div>
      </form>
    </Modal>
  );
}
