import { Chip, link, OVERALL, ProposalCard } from "../components";
import { money, period, termText } from "../format";
import { useHousehold } from "../household";

export function Overview() {
  const { state, helperName } = useHousehold();
  if (!state) return null;
  const pending = state.proposals.filter((p) => p.status === "pending");
  const openBills = state.bills.filter((b) => b.status === "open");
  const doneBills = state.bills.filter((b) => b.status === "approved");

  if (!state.helpers.length && !pending.length) {
    return (
      <div className="empty">
        <h2>Let's get your helpers set up</h2>
        <p>
          Give the assistant whatever you have: your own notes, screenshots of text threads, pasted messages, the bills
          that just came in. It reads them, drafts what you agreed with each person, and checks every bill against it.
        </p>
        <p>You confirm every agreement and every payment. Nothing is saved or approved without your click.</p>
        <p className="muted small">Use the assistant panel to add your first documents.</p>
      </div>
    );
  }

  return (
    <div className="stack-lg">
      <section className="stack">
        <h2>
          Needs your OK {pending.length > 0 && <span className="count">{pending.length}</span>}
        </h2>
        {pending.length === 0 ? (
          <p className="muted">Nothing is waiting for you.</p>
        ) : (
          pending.map((p) => <ProposalCard key={p.proposal_id} p={p} />)
        )}
      </section>

      <section className="stack">
        <h2>Bills to review</h2>
        {openBills.length === 0 ? (
          <p className="muted">No open bills. Paste or upload a bill in the assistant panel.</p>
        ) : (
          <BillTable bills={openBills} helperName={helperName} />
        )}
      </section>

      <section className="stack">
        <h2>Helpers</h2>
        <div className="helper-grid">
          {state.helpers.map((h) => {
            const current = h.versions.at(-1);
            return (
              <a key={h.helper_id} className="card helper-card" href={link(`/helpers/${h.helper_id}`)}>
                <div className="row between">
                  <strong>{h.name}</strong>
                  <span className="muted small">{h.role}</span>
                </div>
                {current ? (
                  <ul className="plain small">
                    {current.terms.map((t) => (
                      <li key={t.code}>
                        <span className="muted">{t.label}:</span> {termText(t, current.terms)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="small muted">No confirmed terms yet</p>
                )}
                {h.payment_method && <p className="small muted">Paid by {h.payment_method}</p>}
              </a>
            );
          })}
        </div>
      </section>

      {doneBills.length > 0 && (
        <section className="stack">
          <h2>Approved</h2>
          <BillTable bills={doneBills} helperName={helperName} />
        </section>
      )}
    </div>
  );
}

function BillTable({ bills, helperName }: { bills: import("../api").BillSummary[]; helperName: (id: string) => string }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Helper</th>
            <th>Period</th>
            <th className="num">Billed</th>
            <th className="num">Agreement says</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {bills.map((b) => (
            <tr key={b.bill_id} className="clickable" onClick={() => (location.hash = `/bills/${b.bill_id}`)}>
              <td>
                <a href={link(`/bills/${b.bill_id}`)}>{helperName(b.helper_id)}</a>
              </td>
              <td>{period(b.period_start, b.period_end)}</td>
              <td className="num">{money(b.stated_total)}</td>
              <td className="num">
                {b.status === "approved" ? (
                  <>Paid {money(b.approved_amount)}</>
                ) : (
                  <>
                    {money(b.expected_total)}
                    {Number(b.undecided_total) > 0 && <span className="muted"> + {money(b.undecided_total)}?</span>}
                  </>
                )}
              </td>
              <td>
                {b.status === "approved" ? (
                  <Chip tone="ok">Approved</Chip>
                ) : (
                  <Chip tone={OVERALL[b.overall].tone}>{OVERALL[b.overall].label}</Chip>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
