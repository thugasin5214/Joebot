import { useState } from "react";
import { api, type AgreementVersion, type BillLine } from "../api";

type Draft = {
  raw: string;
  date: string;
  start: string;
  end: string;
  quantity: string;
  amount: string;
  term_code: string;
  stated_weekday: string | null;
  claimed_justification: string;
};

const toDraft = (l: BillLine): Draft => ({
  raw: l.raw,
  date: l.date ?? "",
  start: l.start?.slice(0, 5) ?? "",
  end: l.end?.slice(0, 5) ?? "",
  quantity: String(l.quantity ?? "1"),
  amount: String(l.amount),
  term_code: l.term_code ?? "",
  stated_weekday: l.stated_weekday,
  claimed_justification: l.claimed_justification ?? "",
});

const isMoney = (s: string) => /^\d+(\.\d{1,2})?$/.test(s.trim());
const isQty = (s: string) => /^\d+(\.\d+)?$/.test(s.trim()) && Number(s) > 0;

/**
 * Fix how a bill was read (a misread date, a wrong amount, a line mapped to the
 * wrong term). The checker then re-prices every line; nothing here changes what
 * was agreed.
 */
export function LineEditor({
  billId,
  lines,
  versions,
  helperName,
  onDone,
}: {
  billId: string;
  lines: BillLine[];
  versions: AgreementVersion[];
  helperName: string;
  onDone: (saved: boolean) => void;
}) {
  const [rows, setRows] = useState<Draft[]>(() => lines.map(toDraft));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Every term ever agreed, by code; the label comes from the newest version.
  const terms = new Map<string, string>();
  for (const v of [...versions].sort((a, b) => a.version - b.version))
    for (const t of v.terms) terms.set(t.code, t.label);

  const set = (i: number, patch: Partial<Draft>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const problems = rows.flatMap((r, i) => {
    const n = i + 1;
    const out: string[] = [];
    if (!r.raw.trim()) out.push(`Line ${n}: describe what was billed.`);
    if (!isMoney(r.amount))
      out.push(`Line ${n}: amount should be like 140 or 22.50.`);
    if (!isQty(r.quantity))
      out.push(`Line ${n}: quantity should be a positive number.`);
    if (!!r.start !== !!r.end)
      out.push(`Line ${n}: give both a start and an end time, or neither.`);
    return out;
  });

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body: BillLine[] = rows.map((r, i) => ({
        line_no: i + 1,
        raw: r.raw.trim(),
        date: r.date || null,
        start: r.start || null,
        end: r.end || null,
        quantity: r.quantity.trim(),
        unit_price: null,
        amount: r.amount.trim(),
        term_code: r.term_code || null,
        // A changed date makes the weekday as written meaningless.
        stated_weekday:
          r.date === (lines[i]?.date ?? "") ? r.stated_weekday : null,
        claimed_justification: r.claimed_justification,
      }));
      await api("PUT", `/api/bills/${billId}/lines`, { lines: body });
      onDone(true);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="card stack">
      <div>
        <h3>Fix how this bill was read</h3>
        <p className="small muted">
          Correct what {helperName}'s bill <em>says</em>, not what you think it
          should say. The check against your agreement runs again when you save.
          Your line decisions are reset, and the edit is recorded in Activity.
        </p>
      </div>
      <ol className="edit-lines">
        {rows.map((r, i) => (
          <li key={i} className="edit-line">
            <label className="field wide">
              <span>Line {i + 1}: what was billed</span>
              <input
                value={r.raw}
                onChange={(e) => set(i, { raw: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Date</span>
              <input
                type="date"
                aria-label={`Line ${i + 1} date`}
                value={r.date}
                onChange={(e) => set(i, { date: e.target.value })}
              />
            </label>
            <label className="field">
              <span>From</span>
              <input
                type="time"
                aria-label={`Line ${i + 1} start`}
                value={r.start}
                onChange={(e) => set(i, { start: e.target.value })}
              />
            </label>
            <label className="field">
              <span>To</span>
              <input
                type="time"
                aria-label={`Line ${i + 1} end`}
                value={r.end}
                onChange={(e) => set(i, { end: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Qty</span>
              <input
                className="num"
                inputMode="decimal"
                aria-label={`Line ${i + 1} quantity`}
                value={r.quantity}
                onChange={(e) => set(i, { quantity: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Amount $</span>
              <input
                className="num"
                inputMode="decimal"
                aria-label={`Line ${i + 1} amount`}
                value={r.amount}
                onChange={(e) => set(i, { amount: e.target.value })}
              />
            </label>
            <label className="field term">
              <span>Agreement term</span>
              <select
                aria-label={`Line ${i + 1} term`}
                value={r.term_code}
                onChange={(e) => set(i, { term_code: e.target.value })}
              >
                <option value="">Not in agreement</option>
                {[...terms].map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
                {r.term_code && !terms.has(r.term_code) && (
                  <option value={r.term_code}>{r.term_code} (unknown)</option>
                )}
              </select>
            </label>
            <button
              className="btn ghost small remove"
              aria-label={`Remove line ${i + 1}`}
              disabled={rows.length === 1}
              onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
            >
              Remove
            </button>
          </li>
        ))}
      </ol>
      <div>
        <button
          className="btn small"
          onClick={() =>
            setRows((rs) => [
              ...rs,
              {
                raw: "",
                date: "",
                start: "",
                end: "",
                quantity: "1",
                amount: "",
                term_code: "",
                stated_weekday: null,
                claimed_justification: "",
              },
            ])
          }
        >
          Add a line
        </button>
      </div>
      {problems.length > 0 && (
        <ul className="small warn-text">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {error && <p className="error-text">{error}</p>}
      <div className="row end">
        <button className="btn" onClick={() => onDone(false)} disabled={busy}>
          Cancel
        </button>
        <button
          className="btn primary"
          onClick={save}
          disabled={busy || problems.length > 0}
        >
          {busy ? "Saving…" : "Save and re-check"}
        </button>
      </div>
    </div>
  );
}
