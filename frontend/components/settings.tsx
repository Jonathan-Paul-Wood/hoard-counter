"use client";

import { useState } from "react";
import {
  Archive,
  ArrowDownToLine,
  CheckCircle2,
  ChevronDown,
  FileUp,
  History,
  Play,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { api, Entity, errorMessage, prettyDate } from "@/lib/api";
import {
  Button,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Modal,
  PageHead,
  Pagination,
  Panel,
  useApp,
  useLoad,
} from "./ui";
import { ResourceTable } from "./records";

export function Settings() {
  const { session, version } = useApp();
  const [tab, setTab] = useState("categories");
  const [runRule, setRunRule] = useState<Entity | null>(null);
  const tabs = [
    ["categories", "Categories"],
    ["tags", "Tags"],
    ["rules", "Rules"],
    ["rates", "Exchange rates"],
    ["data", "Import & export"],
    ["audit", "Audit trail"],
    ...(session.user.role === "admin"
      ? [
          ["currencies", "Currencies"],
          ["hoard", "Hoard"],
          ["users", "People"],
          ["backups", "Backups"],
        ]
      : []),
  ];
  return (
    <>
      <PageHead
        title="Keep things in order"
        subtitle="The little details that make your hoard ledger your own."
      />
      <div className="tabs page-tabs wrap">
        {tabs.map(([v, l]) => (
          <button
            key={v}
            className={tab === v ? "active" : ""}
            onClick={() => setTab(v)}
          >
            {l}
          </button>
        ))}
      </div>
      {tab === "categories" && (
        <ResourceTable
          resource="categories"
          title="Categories"
          heading={false}
          description="One clear category per transaction."
          columns={[
            {
              key: "name",
              label: "Category",
              render: (r) => (
                <span className="name-with-dot">
                  <i style={{ background: r.color || "#315b46" }} />
                  {r.name}
                </span>
              ),
            },
            {
              key: "last_modified",
              label: "Last updated",
              render: (r) => prettyDate(r.last_modified),
            },
          ]}
        />
      )}{" "}
      {tab === "tags" && (
        <ResourceTable
          resource="tags"
          title="Tags"
          heading={false}
          description="Add another way to find and group transactions."
          columns={[
            {
              key: "name",
              label: "Tag",
              render: (r) => <span className="category-chip">{r.name}</span>,
            },
            {
              key: "last_modified",
              label: "Last updated",
              render: (r) => prettyDate(r.last_modified),
            },
          ]}
        />
      )}{" "}
      {tab === "rules" && (
        <ResourceTable
          resource="rules"
          title="Rules"
          heading={false}
          description="A few thoughtful rules keep new entries organized automatically."
          columns={[
            {
              key: "name",
              label: "Rule",
              render: (r) => <strong>{r.name}</strong>,
            },
            { key: "priority", label: "Priority" },
            { key: "enabled", label: "Status" },
            {
              key: "conditions",
              label: "Conditions",
              render: (r) => `${r.conditions?.length || 0} conditions`,
            },
            {
              key: "actions",
              label: "Actions",
              render: (r) => Object.keys(r.actions || {}).join(", "),
            },
          ]}
          customAction={(r) => (
            <button
              className="icon-button"
              title="Preview or apply rule"
              onClick={() => setRunRule(r)}
            >
              <Play size={15} />
            </button>
          )}
        />
      )}{" "}
      {tab === "rates" && (
        <ResourceTable
          resource="rates"
          title="Exchange rates"
          heading={false}
          description="USD per native unit. Each rate applies from its date until the next update."
          columns={[
            { key: "currency", label: "Currency" },
            {
              key: "date",
              label: "Effective date",
              render: (r) => prettyDate(r.date),
            },
            { key: "rate", label: "USD per unit" },
          ]}
        />
      )}{" "}
      {tab === "data" && <DataExchange />} {tab === "audit" && <Audit />}{" "}
      {tab === "currencies" && (
        <ResourceTable
          resource="currencies"
          title="Currencies"
          heading={false}
          description="Built-in currencies are read-only. Add a currency with its native decimal precision."
          columns={[
            { key: "code", label: "Code" },
            { key: "name", label: "Name" },
            { key: "precision", label: "Decimal places" },
          ]}
        />
      )}{" "}
      {tab === "hoard" && <Hoard />}{" "}
      {tab === "users" && (
        <ResourceTable
          resource="users"
          title="Hoard members"
          heading={false}
          description="Separate logins. One shared hoard ledger."
          columns={[
            { key: "username", label: "Username" },
            { key: "role", label: "Role" },
            {
              key: "active",
              label: "Access",
              render: (r) => (r.active ? "Active" : "Inactive"),
            },
          ]}
        />
      )}{" "}
      {tab === "backups" && <Backups />}
      {runRule && <RuleRun rule={runRule} onClose={() => setRunRule(null)} />}
    </>
  );
}

function RuleRun({ rule, onClose }: { rule: Entity; onClose: () => void }) {
  const { notify, refresh } = useApp();
  const [id, setId] = useState("");
  const [transaction, setTransaction] = useState<Entity | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function examine(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const record = await api<Entity>(`/transactions/${id}`);
      setTransaction(record);
      setPreview(
        await api(`/rules/${rule.id}/preview`, {
          method: "POST",
          body: JSON.stringify({ transaction: record }),
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    setBusy(true);
    try {
      await api(`/rules/${rule.id}/reapply`, {
        method: "POST",
        body: JSON.stringify({ transaction_ids: [id] }),
      });
      notify("Rule reapplied.");
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
      title={`Preview “${rule.name}”`}
      subtitle="Inspect the effect on an existing transaction before applying it."
      onClose={onClose}
    >
      <form onSubmit={examine}>
        <div className="form-grid">
          <Field
            label="Transaction UUID"
            wide
            hint="Find UUIDs in the transaction CSV export or audit trail."
          >
            <input
              required
              value={id}
              onChange={(e) => {
                setId(e.target.value);
                setPreview(null);
              }}
            />
          </Field>
        </div>
        <div className="inline-actions">
          <Button variant="secondary" busy={busy}>
            Preview rule
          </Button>
        </div>
      </form>
      {preview && (
        <div className="panel-body">
          <h3>{transaction?.description}</h3>
          <pre className="code-preview">{JSON.stringify(preview, null, 2)}</pre>
        </div>
      )}
      <ErrorBox error={error} />
      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
        {preview && (
          <Button onClick={apply} busy={busy}>
            Apply to this transaction
          </Button>
        )}
      </div>
    </Modal>
  );
}

function DataExchange() {
  const { notify, refresh } = useApp();
  const [kind, setKind] = useState("transactions");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [committing, setCommitting] = useState(false);
  async function examine(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const data = new FormData();
      data.append("file", file);
      data.append("kind", kind);
      setPreview(await api("/imports/preview", { method: "POST", body: data }));
    } catch (e) {
      setError(errorMessage(e));
      notify(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  }
  async function commit() {
    setCommitting(true);
    setError("");
    try {
      const result = await api("/imports/commit", {
        method: "POST",
        body: JSON.stringify({ token: preview.token }),
      });
      notify("Import completed.");
      setPreview(null);
      setFile(null);
      refresh();
    } catch (e) {
      setError(errorMessage(e));
      notify(errorMessage(e), "error");
    } finally {
      setCommitting(false);
    }
  }
  return (
    <>
      <div className="export-grid">
        {[
          {
            format: "archive",
            icon: Archive,
            title: "Complete archive",
            body: "Financial records, attachments, rates, valuations, and supporting records in one portable ZIP.",
          },
          {
            format: "accounts",
            icon: ArrowDownToLine,
            title: "Account CSV",
            body: "Editable account records with opening and current balances clearly separated.",
          },
          {
            format: "transactions",
            icon: ArrowDownToLine,
            title: "Transaction CSV",
            body: "Portable transaction records, UUIDs, modification dates, and classifications.",
          },
        ].map(({ format, icon: Icon, title, body }) => (
          <Panel key={format}>
            <div className="export-card">
              <div className="empty-icon">
                <Icon size={24} />
              </div>
              <h3>{title}</h3>
              <p>{body}</p>
              <a
                className="button secondary"
                href={`/api/v1/exports?format=${format}`}
              >
                <ArrowDownToLine size={16} />
                Download {format === "archive" ? "archive" : "CSV"}
              </a>
            </div>
          </Panel>
        ))}
      </div>
      <Panel
        title="Bring your records in"
        subtitle="Review changes before anything is written to your ledger."
      >
        <form onSubmit={examine}>
          <div className="panel-body">
            <div className="import-fields">
              <Field label="Import format">
                <select
                  value={kind}
                  onChange={(e) => {
                    setKind(e.target.value);
                    setPreview(null);
                  }}
                >
                  <option value="transactions">Transactions CSV</option>
                  <option value="accounts">Accounts CSV</option>
                  <option value="archive">Complete archive ZIP</option>
                </select>
              </Field>
              <Field label="Choose a file">
                <input
                  type="file"
                  required
                  accept={kind === "archive" ? ".zip" : ".csv,text/csv"}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] || null);
                    setPreview(null);
                  }}
                />
              </Field>
              <Button busy={busy} disabled={!file}>
                <FileUp size={16} />
                Preview import
              </Button>
            </div>
            <p className="muted small">
              UUIDs keep imports idempotent. A newer modification date replaces
              the whole record; conflicts are surfaced for review. Exports also
              serve as import templates.
            </p>
            <ErrorBox error={error} />
          </div>
        </form>
        {preview && (
          <div className="import-preview">
            <h3>Review your import</h3>
            <div className="import-summary">
              {typeof preview.summary === "object" ? (
                Object.entries(preview.summary || {}).map(([k, v]) => (
                  <div key={k}>
                    <strong>{String(v)}</strong>
                    <span>{k.replace(/_/g, " ")}</span>
                  </div>
                ))
              ) : (
                <p>{String(preview.summary)}</p>
              )}
            </div>
            {preview.errors?.length > 0 && (
              <div className="error-box">
                <div>
                  <strong>Resolve these errors before importing</strong>
                  <ul>
                    {preview.errors.map((v: any, i: number) => (
                      <li key={i}>
                        {typeof v === "string" ? v : JSON.stringify(v)}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
            {preview.conflicts?.length > 0 && (
              <div className="notice">
                <strong>Conflicting records</strong>
                <ul>
                  {preview.conflicts.map((v: any, i: number) => (
                    <li key={i}>
                      {typeof v === "string" ? v : JSON.stringify(v)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {preview.changes?.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Record</th>
                      <th>Action</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.changes.map((change: any, i: number) => (
                      <tr key={i}>
                        <td>
                          {change.resource || change.kind || change.id || i + 1}
                        </td>
                        <td>{change.action || change.status}</td>
                        <td>
                          <code>
                            {change.description ||
                              change.name ||
                              JSON.stringify(change)}
                          </code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="form-bottom">
              <Button
                busy={committing}
                disabled={
                  !!preview.errors?.length ||
                  !!preview.conflicts?.length ||
                  !preview.token
                }
                onClick={commit}
              >
                <CheckCircle2 size={16} />
                Commit import
              </Button>
            </div>
          </div>
        )}
      </Panel>
    </>
  );
}

function Audit() {
  const { version } = useApp();
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data, loading, error } = useLoad<any>(
    `/audit?page=${page}&page_size=25`,
    { items: [], total: 0 },
    version,
  );
  return (
    <Panel
      title="Audit trail"
      subtitle="An immutable history of changes to your hoard records."
    >
      <ErrorBox error={error} />
      {loading ? (
        <Loading />
      ) : data.items.length ? (
        <div className="audit-list">
          {data.items.map((event: any) => (
            <div className="audit-event" key={event.id}>
              <button
                className="audit-main"
                onClick={() =>
                  setExpanded(expanded === event.id ? null : event.id)
                }
              >
                <div className="audit-icon">
                  <History size={17} />
                </div>
                <div>
                  <strong>
                    {event.action || event.operation || "Updated"}{" "}
                    {event.resource ||
                      event.entity_type ||
                      event.kind ||
                      "record"}
                  </strong>
                  <p>
                    {event.actor_username ||
                      event.actor ||
                      event.actor_id ||
                      event.user_id ||
                      "System"}{" "}
                    · {event.origin || "manual"} ·{" "}
                    {new Date(
                      event.timestamp ||
                        event.created_at ||
                        event.last_modified,
                    ).toLocaleString()}
                  </p>
                  <code>{event.record_id || event.entity_id}</code>
                </div>
                <ChevronDown
                  className={expanded === event.id ? "rotate" : ""}
                  size={17}
                />
              </button>
              {expanded === event.id && (
                <div className="audit-detail">
                  <div>
                    <h4>Before</h4>
                    <pre>
                      {JSON.stringify(
                        event.before ?? event.before_data ?? null,
                        null,
                        2,
                      )}
                    </pre>
                  </div>
                  <div>
                    <h4>After</h4>
                    <pre>
                      {JSON.stringify(
                        event.after ?? event.after_data ?? null,
                        null,
                        2,
                      )}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <Empty
          title="A clean slate"
          message="Changes will appear here as you use your ledger."
        />
      )}
      <Pagination page={page} total={data.total} onChange={setPage} />
    </Panel>
  );
}

function Hoard() {
  const { version } = useApp();
  const { data, loading, error } = useLoad<any>("/settings", null, version);
  return (
    <Panel
      title="Hoard settings"
      subtitle="Preferences shared by everyone in your hoard."
    >
      <ErrorBox error={error} />
      {loading ? <Loading /> : data && <HoardForm settings={data} />}
    </Panel>
  );
}
function HoardForm({ settings }: { settings: any }) {
  const { notify, refresh } = useApp();
  const [timezone, setTimezone] = useState(
    settings.timezone || "America/New_York",
  );
  const [name, setName] = useState(settings.household_name || "Our hoard");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/settings", {
        method: "PATCH",
        body: JSON.stringify({
          timezone,
          household_name: name,
          revision: settings.revision,
        }),
      });
      notify("Hoard settings saved.");
      refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={save}>
      <div className="panel-body">
        <div className="form-grid">
          <Field label="Hoard name">
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field
            label="Timezone"
            hint="IANA timezone, for example America/New_York."
          >
            <input
              required
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
            />
          </Field>
          <Field label="Reporting currency">
            <input value="USD" readOnly />
          </Field>
          <Field label="Application version">
            <input
              readOnly
              value={settings.app_version || settings.version || "1.0.0"}
            />
          </Field>
        </div>
        <ErrorBox error={error} />
        <div className="form-bottom">
          <Button busy={busy}>Save preferences</Button>
        </div>
      </div>
    </form>
  );
}
function Backups() {
  const { version } = useApp();
  const { data, loading, error } = useLoad<any>(
    "/backups/status",
    null,
    version,
  );
  return (
    <Panel
      title="A safe place to fall back on"
      subtitle="Local recovery snapshots, independent of software upgrades."
    >
      <ErrorBox error={error} />
      {loading ? (
        <Loading />
      ) : (
        data && (
          <div className="panel-body">
            <div className="backup-status">
              <ShieldCheck size={40} strokeWidth={1.2} />
              <div>
                <h3>
                  {data.last_success || data.last_successful_backup
                    ? "Your latest recovery point"
                    : "No backup recorded yet"}
                </h3>
                <p>
                  {data.last_success || data.last_successful_backup
                    ? String(data.last_success || data.last_successful_backup)
                    : "The weekly backup appears here after the host schedule runs."}
                </p>
              </div>
            </div>
            {data.last_error && <ErrorBox error={String(data.last_error)} />}
            <div className="retention-grid">
              <div>
                <strong>4</strong>
                <span>weekly buckets</span>
              </div>
              <div>
                <strong>3</strong>
                <span>completed months</span>
              </div>
              <div>
                <strong>3</strong>
                <span>completed quarters</span>
              </div>
              <div>
                <strong>All</strong>
                <span>annual backups</span>
              </div>
            </div>
            <p className="muted">
              Weekly backups run on Sunday at 03:00 in your configured hoard
              timezone. Each verified snapshot includes the SQLite database and
              uploaded files. Backups are stored on this machine.
            </p>
            {data.backups?.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Backup</th>
                      <th>Created</th>
                      <th>Size</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.backups.map((b: any, i: number) => (
                      <tr key={i}>
                        <td>{b.name || b.filename}</td>
                        <td>{b.created_at || b.date}</td>
                        <td>
                          {b.size
                            ? `${(b.size / 1024 / 1024).toFixed(1)} MB`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      )}
    </Panel>
  );
}
