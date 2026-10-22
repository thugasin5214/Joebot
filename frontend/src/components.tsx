import { useEffect, useRef, useState } from "react";
import { api, type AgreementVersion, type Proposal, type Term } from "./api";
import { day, diffTerms, money } from "./format";
import { useHousehold } from "./household";

export function Chip({ tone, children }: { tone: "ok" | "warn" | "bad" | "info" | "muted"; children: React.ReactNode }) {
  return <span className={`chip ${tone}`}>{children}</span>;
}

export const OVERALL: Record<string, { tone: "ok" | "warn" | "bad"; label: string }> = {
  matches: { tone: "ok", label: "Matches agreement" },
  discrepancies: { tone: "bad", label: "Doesn't match" },
  needs_review: { tone: "warn", label: "Needs your decision" },
};

export function link(path: string) {
  return `#${path}`;
}

// ------------------------------------------------------------------ dialog

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  onConfirm,
  onClose,
  danger,
}: {
  title: string;
  children: React.ReactNode;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  danger?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} className="dialog" onClose={onClose} onCancel={onClose}>
      <h3>{title}</h3>
      <div className="dialog-body">{children}</div>
      {error && <p className="error-text">{error}</p>}
      <div className="row end">
        <button className="btn" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button
          className={`btn ${danger ? "danger" : "primary"}`}
          disabled={busy}
          autoFocus
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await onConfirm();
              onClose();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}

// ------------------------------------------------------------------ terms

export function TermsTable({ terms, previous }: { terms: Term[]; previous?: Term[] }) {
  const changes = diffTerms(previous, terms);
  return (
    <table className="terms">
      <tbody>
        {changes.map((c) => (
          <tr key={c.label + (c.after ?? c.before)}>
            <th scope="row">{c.label}</th>
            <td>
              {c.before && c.after && (
                <>
                  <s className="muted">{c.before}</s> <span aria-hidden>→</span>{" "}
                </>
              )}
              {c.after ? (
                <span className={c.before ? "changed" : undefined}>{c.after}</span>
              ) : (
                <span className="muted">
                  <s>{c.before}</s> (dropped)
                </span>
              )}
              {previous && !c.before && c.after && <Chip tone="info">new</Chip>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function VersionDetails({ v, sourceTitle }: { v: Partial<AgreementVersion>; sourceTitle: (id: string) => string }) {
  return (
    <div className="stack-sm">
      {v.schedule && (v.schedule.days?.length || v.schedule.description) ? (
        <p className="small muted">
          Usual schedule: {v.schedule.description || v.schedule.days!.map((d) => d[0].toUpperCase() + d.slice(1)).join(", ")}
          {v.schedule.skips_us_holidays ? " (not on holidays)" : ""}
        </p>
      ) : null}
      {v.date_notes?.map((n) => (
        <p key={n.on} className="small">
          <strong>{day(n.on)}:</strong> {n.note}
        </p>
      ))}
      {v.notes?.map((n) => (
        <p key={n} className="small muted">
          {n}
        </p>
      ))}
      {!!v.sources?.length && (
        <div className="quotes">
          {v.sources.map((s, i) => (
            <blockquote key={i}>
              “{s.quote}”<cite>{sourceTitle(s.source_id)}</cite>
            </blockquote>
          ))}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------- proposals

export function ProposalCard({ p, compact }: { p: Proposal; compact?: boolean }) {
  const { refresh, helperName, state } = useHousehold();
  const [dialog, setDialog] = useState<"confirm" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const sourceTitle = (id: string) => state?.sources.find((s) => s.source_id === id)?.title ?? "document";

  const status =
    p.status === "pending" ? (
      <Chip tone="warn">Waiting for you</Chip>
    ) : p.status === "confirmed" ? (
      <Chip tone="ok">{p.kind === "approval" ? "Approved" : "Saved"}</Chip>
    ) : (
      <Chip tone="muted">Rejected</Chip>
    );

  let body: React.ReactNode;
  let confirmTitle = "";
  let confirmBody: React.ReactNode = null;
  let confirmLabel = "";

  if (p.kind === "agreement") {
    const a = p.payload.agreement;
    const prev: AgreementVersion | null = p.preview.previous;
    const name = p.payload.new_helper?.name ?? helperName(a.helper_id);
    confirmTitle = p.payload.new_helper ? `Add ${name} with these terms?` : `Save new terms for ${name}?`;
    confirmLabel = "Save terms";
    confirmBody = (
      <p>
        From <strong>{day(a.effective_from)}</strong>, bills from {name} will be checked against these terms. Earlier
        terms stay on record. Approved bills don't change.
      </p>
    );
    body = (
      <>
        <div className="kv">
          {p.payload.new_helper && (
            <span>
              New helper: <strong>{name}</strong>, {p.payload.new_helper.role}
            </span>
          )}
          <span>
            In effect from <strong>{day(a.effective_from)}</strong>
          </span>
          {a.agreed_on && <span>Agreed {day(a.agreed_on)}</span>}
          {a.basis === "recollection" && <Chip tone="warn">From your notes, not in writing</Chip>}
        </div>
        <TermsTable terms={a.terms} previous={prev?.terms} />
        {!compact && <VersionDetails v={a} sourceTitle={sourceTitle} />}
        {p.payload.conflicts?.length > 0 && (
          <div className="callout warn">
            <strong>Your inputs disagree</strong>
            <ul>
              {p.payload.conflicts.map((c: string) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        )}
        {p.status === "pending" && p.preview.open_bill_impact?.length > 0 && (
          <div className="callout info">
            <strong>If you save this</strong>
            <ul>
              {p.preview.open_bill_impact.map((i: any) => (
                <li key={i.bill_id}>
                  <a href={link(`/bills/${i.bill_id}`)}>Open bill</a>: agreement would say {money(i.after)} instead of{" "}
                  {money(i.before)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </>
    );
  } else {
    const bill = state?.bills.find((b) => b.bill_id === p.payload.bill_id);
    const name = bill ? helperName(bill.helper_id) : "this helper";
    confirmTitle = `Approve ${money(p.payload.amount)} for ${name}?`;
    confirmLabel = `Approve ${money(p.payload.amount)}`;
    confirmBody = <p>Once approved, this bill is locked and can't be edited. Nothing is paid automatically.</p>;
    body = (
      <>
        <p>
          Pay <strong>{money(p.payload.amount)}</strong> on <a href={link(`/bills/${p.payload.bill_id}`)}>this bill</a>
          {p.payload.note ? ` — “${p.payload.note}”` : ""}
        </p>
      </>
    );
  }

  return (
    <article className="card proposal">
      <header className="row between">
        <h4>{p.summary}</h4>
        {status}
      </header>
      {body}
      {p.status === "rejected" && p.reason && <p className="small muted">You said: “{p.reason}”</p>}
      {p.status === "pending" && (
        <div className="row">
          <button className="btn primary" onClick={() => setDialog("confirm")}>
            {p.kind === "approval" ? "Review & approve" : "Save these terms"}
          </button>
          <button className="btn" onClick={() => setDialog("reject")}>
            Not right
          </button>
        </div>
      )}
      {dialog === "confirm" && (
        <ConfirmDialog
          title={confirmTitle}
          confirmLabel={confirmLabel}
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await api("POST", `/api/proposals/${p.proposal_id}/confirm`);
            await refresh();
          }}
        >
          {confirmBody}
        </ConfirmDialog>
      )}
      {dialog === "reject" && (
        <ConfirmDialog
          title="What's wrong with it?"
          confirmLabel="Reject"
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await api("POST", `/api/proposals/${p.proposal_id}/reject`, { reason });
            await refresh();
          }}
        >
          <label className="field">
            <span>Optional: tell the assistant so it can try again</span>
            <textarea id={`reason-${p.proposal_id}`} value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
          </label>
        </ConfirmDialog>
      )}
    </article>
  );
}
