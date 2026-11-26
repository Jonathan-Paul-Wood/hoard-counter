"use client";

import { useEffect, useState } from "react";
import type { Settings, OcrCapabilities } from "@/lib/generated";
import { api, errorMessage } from "@/lib/api";
import { Button, ErrorBox, Field, Loading, Panel, useApp, useLoad } from "./ui";

export function OcrSettings() {
  const { refresh, notify, version } = useApp();
  const {
    data,
    loading,
    error: loadError,
  } = useLoad<Settings | null>("/settings", null, version);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  useEffect(() => {
    if (data) {
      setUrl(data.ocr_url || "");
      setToken("");
      setClearToken(false);
    }
  }, [data]);
  async function save(disconnect = false) {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await api("/settings", {
        method: "PATCH",
        body: JSON.stringify({
          ocr_url: disconnect ? "" : url,
          ...(disconnect || clearToken
            ? { ocr_token: "" }
            : token
              ? { ocr_token: token }
              : {}),
        }),
      });
      setToken("");
      setClearToken(false);
      refresh();
      notify(
        disconnect
          ? "OCR Processor disconnected."
          : "OCR Processor settings saved.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      const result = await api<OcrCapabilities>("/ocr/test", {
        method: "POST",
      });
      setStatus(
        `Connected. SystemOne transaction extraction is ready.${result.ocr_available ? "" : " Native OCR is unavailable; scanned documents may fail."}`,
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  if (loading) return <Loading />;
  if (loadError) return <ErrorBox error={loadError} />;
  return (
    <Panel
      title="OCR Processor"
      subtitle="Connect an independently running OCR Processor to import statements, CSV lists and receipts."
    >
      <form
        className="panel-body"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="form-grid">
          <Field
            label="Service URL"
            hint="Use the address reachable from Hoard Counter’s backend, for example http://processor:8081."
            wide
          >
            <input
              aria-label="OCR Processor URL"
              type="url"
              maxLength={2048}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={busy}
              placeholder="http://127.0.0.1:8081"
            />
          </Field>
          <Field
            label="Bearer token (optional)"
            hint={
              data?.ocr_token_saved
                ? "A token is saved. Leave blank to keep it."
                : "The standard OCR Processor requires its OCR_API_KEY."
            }
            wide
          >
            <input
              aria-label="OCR bearer token"
              type="password"
              autoComplete="new-password"
              maxLength={4096}
              value={token}
              disabled={busy || clearToken}
              onChange={(e) => setToken(e.target.value)}
            />
          </Field>
          {data?.ocr_token_saved && (
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={clearToken}
                disabled={busy}
                onChange={(e) => setClearToken(e.target.checked)}
              />{" "}
              Clear saved token
            </label>
          )}
        </div>
        <p className="muted small">
          Save changes before testing. Uploaded files, account details,
          categories and rules are sent to this service and its SystemOne model.
          Configure its SystemOne connection separately.
        </p>
        {status && (
          <p className="notice" role="status">
            {status}
          </p>
        )}
        <ErrorBox error={error} />
        <div className="transaction-actions">
          <Button type="submit" busy={busy}>
            Save connection
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={busy || !data?.ocr_configured}
            onClick={test}
          >
            Test connection
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={busy || !data?.ocr_configured}
            onClick={() => save(true)}
          >
            Disconnect
          </Button>
        </div>
      </form>
    </Panel>
  );
}
