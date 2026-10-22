import { useEffect, useState } from "react";
import { api } from "../api";
import { Chip } from "../components";

type Entry = { ts: string; actor: string; action: string; ref: string; detail: string };

const LABELS: Record<string, string> = {
  added_source: "Added a document",
  proposed_agreement: "Proposed terms",
  proposed_approval: "Proposed an approval",
  added_helper: "Added a helper",
  confirmed_agreement: "Saved terms",
  rejected_proposal: "Rejected a proposal",
  recorded_bill: "Recorded a bill",
  corrected_bill: "Corrected a bill",
  approved_bill: "Approved a bill",
};

export function Activity() {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  useEffect(() => {
    api<Entry[]>("GET", "/api/audit").then(setEntries);
  }, []);
  if (!entries) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      <h2>Activity</h2>
      <p className="muted">Everything that changed, and who did it. Only you can save terms or approve bills.</p>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>What</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.ts + e.ref}>
                <td className="nowrap small">{new Date(e.ts).toLocaleString()}</td>
                <td>{e.actor === "jordan" ? <Chip tone="info">You</Chip> : <Chip tone="muted">Assistant</Chip>}</td>
                <td>{LABELS[e.action] ?? e.action}</td>
                <td className="small">
                  {e.detail} <span className="muted">{e.ref}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
