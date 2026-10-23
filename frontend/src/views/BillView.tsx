import { useCallback, useEffect, useState } from "react";
import { api, type BillDetail, type Choice, type LineCheck } from "../api";
import { Chip, ConfirmDialog, link, OVERALL } from "../components";
import { day, money, period, signedMoney } from "../format";
import { useHousehold } from "../household";
import { LineEditor } from "./LineEditor";
import { SourceViewer } from "./SourceViewer";

const LINE: Record<
  LineCheck["status"],
  { tone: "ok" | "warn" | "bad" | "info"; label: string }
> = {
  match: { tone: "ok", label: "Matches" },
  overcharge: { tone: "bad", label: "Over" },
  undercharge: { tone: "info", label: "Under" },
  needs_decision: { tone: "warn", label: "Not in agreement" },
  cannot_check: { tone: "warn", label: "Can't check" },
  ambiguous: { tone: "warn", label: "Ambiguous" },
};

export function BillView({ billId }: { billId: string }) {
  const { refresh, helperName, askAssistant } = useHousehold();
  const [d, setD] = useState<BillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Choice>>({});
  const [quote, setQuote] = useState<{
    amount: string;
    undecided: string[];
  } | null>(null);
  const [note, setNote] = useState("");
  const [approving, setApproving] = useState(false);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      const detail = await api<BillDetail>("GET", `/api/bills/${billId}`);
      setD(detail);
      setDecisions(detail.default_decisions);
      setQuote({ amount: detail.default_amount, undecided: detail.undecided });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [billId]);

  useEffect(() => {
    load();
  }, [load]);

  async function choose(lineNo: number, c: Choice) {
    const next = { ...decisions, [lineNo]: c };
    setDecisions(next);
    setQuote(
      await api("POST", `/api/bills/${billId}/quote`, { decisions: next }),
    );
  }

  if (error) return <p className="error-text">{error}</p>;
  if (!d) return <p className="muted">Loading…</p>;

  const { bill, check } = d;
  const name = helperName(bill.helper_id);
  const approved = bill.status === "approved";
  const overall = OVERALL[check.overall];
  const billed = check.stated_total ?? check.lines_sum;

  return (
    <div className="stack-lg">
      <a href={link("/")} className="small">
        ← Overview
      </a>
      <header className="stack-sm">
        <div className="row between wrap">
          <h2>
            {name} · {period(bill.period_start, bill.period_end)}
          </h2>
          {approved ? (
            <Chip tone="ok">Approved · locked</Chip>
          ) : bill.status === "superseded" ? (
            <Chip tone="muted">Replaced by a newer bill</Chip>
          ) : (
            <Chip tone={overall.tone}>{overall.label}</Chip>
          )}
        </div>
        <p className="muted small">
          {bill.received_on
            ? `Received ${day(bill.received_on)}`
            : "Received date unknown"}
          {bill.source_id ? " · original below" : ""}
          {bill.corrected_at
            ? ` · lines corrected by you ${day(bill.corrected_at)}`
            : ""}
        </p>
      </header>

      {!approved && d.pending_terms.length > 0 && (
        <div className="callout warn">
          <strong>
            Hold on: a change to {name}'s terms is waiting for your OK
          </strong>
          <ul>
            {d.pending_terms.map((p) => (
              <li key={p.proposal_id}>
                {p.summary}
                {p.after && (
                  <> — this bill would be checked at {money(p.after)}</>
                )}
              </li>
            ))}
          </ul>
          <a href={link("/")}>Review it first</a>, then come back to approve.
        </div>
      )}

      <Verdict d={d} name={name} />

      {check.flags.length > 0 && (
        <ul className="flags">
          {check.flags.map((f) => (
            <li key={f.message} className={`flag ${f.level}`}>
              {f.message}
            </li>
          ))}
        </ul>
      )}

      {editing ? (
        <LineEditor
          billId={billId}
          lines={bill.lines}
          versions={d.versions}
          helperName={name}
          onDone={async (saved) => {
            setEditing(false);
            if (saved) await Promise.all([load(), refresh()]);
          }}
        />
      ) : (
        <div className="table-wrap">
          <table className="data lines">
            <thead>
              <tr>
                <th>What was billed</th>
                <th className="num">Billed</th>
                <th className="num">Agreed</th>
                <th className="num">Diff</th>
                <th>Check</th>
                {!approved && <th>Pay</th>}
              </tr>
            </thead>
            <tbody>
              {check.lines.map((l) => {
                const st = LINE[l.status];
                const needsCall = !["match"].includes(l.status);
                return (
                  <tr
                    key={l.line_no}
                    className={l.status === "match" ? "" : "attention"}
                  >
                    <td>
                      <div className="raw">{l.raw}</div>
                      <ul className="steps">
                        {l.steps.map((s) => (
                          <li key={s}>{s}</li>
                        ))}
                        {l.flags.map((f) => (
                          <li
                            key={f.message}
                            className={`flag-inline ${f.level}`}
                          >
                            {f.message}
                          </li>
                        ))}
                      </ul>
                    </td>
                    <td className="num">{money(l.billed)}</td>
                    <td className="num">{money(l.expected)}</td>
                    <td className="num">{signedMoney(l.delta)}</td>
                    <td>
                      <Chip tone={st.tone}>{st.label}</Chip>
                    </td>
                    {!approved && (
                      <td>
                        {needsCall ? (
                          <select
                            id={`line-${l.line_no}`}
                            aria-label={`Decision for line ${l.line_no}`}
                            value={decisions[l.line_no] ?? ""}
                            onChange={(e) =>
                              choose(l.line_no, e.target.value as Choice)
                            }
                            className={decisions[l.line_no] ? "" : "needs"}
                          >
                            {!decisions[l.line_no] && (
                              <option value="">Decide…</option>
                            )}
                            {l.expected !== null && (
                              <option value="agreement">
                                Agreed {money(l.expected)}
                              </option>
                            )}
                            <option value="billed">
                              As billed {money(l.billed)}
                            </option>
                            <option value="exclude">Don't pay</option>
                          </select>
                        ) : (
                          <span className="muted small">
                            {money(l.expected)}
                          </span>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <td>Lines add up to</td>
                <td className="num">{money(check.lines_sum)}</td>
                <td colSpan={approved ? 3 : 4} />
              </tr>
              {check.stated_total !== null && (
                <tr className={check.arithmetic_ok ? "" : "attention"}>
                  <td>{name}'s total says</td>
                  <td className="num">{money(check.stated_total)}</td>
                  <td colSpan={approved ? 3 : 4}>
                    {!check.arithmetic_ok && (
                      <Chip tone="bad">Doesn't add up</Chip>
                    )}
                  </td>
                </tr>
              )}
            </tfoot>
          </table>
        </div>
      )}

      {bill.status === "open" && !editing && (
        <p className="small muted">
          Something read wrong (a date, an amount, the wrong service)?{" "}
          <button className="btn small" onClick={() => setEditing(true)}>
            Fix how this bill was read
          </button>
        </p>
      )}

      {approved ? (
        <div className="card locked">
          <p>
            <strong>Approved {money(bill.approval.amount)}</strong> on{" "}
            {day(bill.approval.approved_at)} by Jordan.
            {bill.approval.note && <> Note: “{bill.approval.note}”</>}
          </p>
          <p className="small muted">
            This bill is locked. Checked against agreement version{" "}
            {bill.approval.agreement_versions.join(", ")}. Pay {name} yourself;
            this app doesn't move money.
          </p>
        </div>
      ) : bill.status === "open" && !editing ? (
        <div className="card approve-box stack">
          <div className="row between wrap">
            <div>
              <div className="eyebrow">You'll pay</div>
              <div className="big-money">
                {quote ? money(quote.amount) : "—"}
              </div>
              {quote && Number(quote.amount) !== Number(billed) && (
                <div className="small muted">
                  {name} asked for {money(billed)} (
                  {signedMoney(String(Number(quote.amount) - Number(billed)))})
                </div>
              )}
            </div>
            <div className="row wrap">
              <button
                className="btn"
                onClick={() =>
                  askAssistant(
                    `Draft a short, friendly message I could send ${name} about bill ${bill.bill_id}, explaining any differences from what we agreed.`,
                  )
                }
              >
                Draft a message to {name}
              </button>
              <button
                className="btn primary"
                disabled={
                  !quote ||
                  quote.undecided.length > 0 ||
                  d.pending_terms.length > 0
                }
                onClick={() => setApproving(true)}
              >
                Approve {quote ? money(quote.amount) : ""}…
              </button>
            </div>
          </div>
          {quote && quote.undecided.length > 0 && (
            <p className="small warn-text">
              Decide the highlighted lines before approving.
            </p>
          )}
          <label className="field">
            <span>Note for your records (optional)</span>
            <input
              id="approve-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Paid $140 for Sep 1 and 8, told Lupe"
            />
          </label>
        </div>
      ) : null}

      {d.source && <SourceViewer source={d.source} />}

      {approving && quote && (
        <ConfirmDialog
          title={`Approve ${money(quote.amount)} for ${name}?`}
          confirmLabel={`Approve ${money(quote.amount)}`}
          onClose={() => setApproving(false)}
          onConfirm={async () => {
            await api("POST", `/api/bills/${billId}/approve`, {
              decisions,
              amount: quote.amount,
              note,
              confirm: true,
            });
            await Promise.all([load(), refresh()]);
          }}
        >
          <p>
            {period(bill.period_start, bill.period_end)}, {name} billed{" "}
            {money(billed)}. You're approving{" "}
            <strong>{money(quote.amount)}</strong>.
          </p>
          <p>
            Once approved this bill is locked and can't be edited. This app
            doesn't send money; pay {name} as usual.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function Verdict({ d, name }: { d: BillDetail; name: string }) {
  const c = d.check;
  const billed = c.stated_total ?? c.lines_sum;
  let title: string;
  let detail: string;
  if (c.overall === "matches") {
    title = `Yes, this is what you agreed: ${money(c.expected_total)}`;
    detail = "Every line matches the agreement in effect on its date.";
  } else if (c.overall === "discrepancies") {
    const diff = Number(billed) - Number(c.expected_total);
    title = `No. ${name} billed ${money(billed)}; your agreement says ${money(c.expected_total)}`;
    detail =
      diff > 0
        ? `That's ${money(diff)} more than agreed.`
        : `That's ${money(-diff)} less than agreed.`;
  } else {
    title = `Not fully covered by your agreement`;
    detail = `The agreement supports ${money(c.expected_total)}. ${money(c.undecided_total)} needs your decision.`;
  }
  return (
    <div className={`verdict ${c.overall}`}>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}
