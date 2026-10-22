import { Chip, link, OVERALL, TermsTable, VersionDetails } from "../components";
import { day, money, period } from "../format";
import { useHousehold } from "../household";

export function Helpers({ helperId }: { helperId?: string }) {
  const { state } = useHousehold();
  if (!state) return null;
  const helper = state.helpers.find((h) => h.helper_id === helperId) ?? state.helpers[0];
  if (!helper) return <p className="muted">No helpers yet. Give the assistant your notes or a text thread to start.</p>;
  const sourceTitle = (id: string) => state.sources.find((s) => s.source_id === id)?.title ?? "document";
  const today = new Date().toISOString().slice(0, 10);
  const versions = [...helper.versions].reverse();
  const inEffect = helper.versions.filter((v) => v.effective_from <= today).at(-1);
  const bills = state.bills.filter((b) => b.helper_id === helper.helper_id);
  const pending = state.proposals.filter((p) => p.status === "pending" && p.payload?.agreement?.helper_id === helper.helper_id);

  return (
    <div className="stack-lg">
      <nav className="tabs" aria-label="Helpers">
        {state.helpers.map((h) => (
          <a key={h.helper_id} href={link(`/helpers/${h.helper_id}`)} className={h.helper_id === helper.helper_id ? "active" : ""}>
            {h.name}
          </a>
        ))}
      </nav>

      <header className="stack-sm">
        <h2>{helper.name}</h2>
        <p className="muted">
          {helper.role}
          {helper.payment_method && ` · paid by ${helper.payment_method}`}
        </p>
        {pending.length > 0 && (
          <p>
            <a href={link("/")}>{pending.length} change{pending.length > 1 ? "s" : ""} waiting for your OK</a>
          </p>
        )}
      </header>

      <section className="stack">
        <h3>What you agreed</h3>
        <ol className="timeline">
          {versions.map((v, i) => {
            const prev = versions[i + 1];
            return (
              <li key={v.version} className="card stack-sm">
                <div className="row between wrap">
                  <strong>From {day(v.effective_from)}</strong>
                  <span className="row">
                    {v === inEffect && <Chip tone="ok">In effect</Chip>}
                    {v.effective_from > today && <Chip tone="info">Upcoming</Chip>}
                    {v.basis === "recollection" && <Chip tone="warn">From your notes</Chip>}
                    <span className="muted small">v{v.version}</span>
                  </span>
                </div>
                <TermsTable terms={v.terms} previous={prev?.terms} />
                <VersionDetails v={v} sourceTitle={sourceTitle} />
                <p className="small muted">
                  {v.agreed_on && `Agreed ${day(v.agreed_on)} · `}Saved by you {day(v.confirmed_at)}
                </p>
              </li>
            );
          })}
        </ol>
      </section>

      {bills.length > 0 && (
        <section className="stack">
          <h3>Bills</h3>
          <ul className="plain stack-sm">
            {bills.map((b) => (
              <li key={b.bill_id} className="row between">
                <a href={link(`/bills/${b.bill_id}`)}>{period(b.period_start, b.period_end)}</a>
                <span className="row">
                  {b.status === "approved" ? (
                    <Chip tone="ok">Approved {money(b.approved_amount)}</Chip>
                  ) : b.status === "superseded" ? (
                    <Chip tone="muted">Replaced</Chip>
                  ) : (
                    <Chip tone={OVERALL[b.overall].tone}>{OVERALL[b.overall].label}</Chip>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
