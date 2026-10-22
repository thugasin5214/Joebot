import type { Term } from "./api";

export function money(x: string | number | null | undefined): string {
  if (x === null || x === undefined || x === "") return "—";
  const n = Number(x);
  const whole = Number.isInteger(n);
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  });
}

export function signedMoney(x: string | null): string {
  if (x === null) return "—";
  const n = Number(x);
  if (n === 0) return "—";
  return (n > 0 ? "+" : "−") + money(Math.abs(n));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

export function period(start: string, end: string): string {
  const [y, m] = start.split("-").map(Number);
  const [y2, m2] = end.split("-").map(Number);
  const full = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  if (y === y2 && m === m2) return `${full[m - 1]} ${y}`;
  return `${day(start)} – ${day(end)}`;
}

export function clock(t: string | null): string {
  if (!t) return "";
  const [h, m] = t.split(":").map(Number);
  const hh = h % 12 || 12;
  const ap = h < 12 ? "am" : "pm";
  return m ? `${hh}:${String(m).padStart(2, "0")}${ap}` : `${hh}${ap}`;
}

const DAYS: Record<string, string> = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };

export function termText(t: Term, all: Term[]): string {
  if (t.kind === "per_unit") return `${money(t.amount)} per ${t.unit}`;
  if (t.kind === "package") {
    const over = all.find((x) => x.code === t.overflow_term);
    let s = `${money(t.amount)} flat`;
    if (t.covers_until) s += ` until ${clock(t.covers_until)}`;
    if (over) s += `, then ${over.label.toLowerCase()} rates`;
    return s;
  }
  const parts = [`${money(t.base_rate)}/hr`];
  for (const tier of t.tiers) parts.push(`${money(tier.rate)}/hr ${clock(tier.start)}–${clock(tier.end)}`);
  for (const m of t.day_multipliers) {
    let s = `${DAYS[m.day] ?? m.day} ×${Number(m.multiplier)}`;
    if (m.applies_to === "base") s += " (base rate only)";
    if (m.applies_to === null && t.tiers.length) s += " (late-rate interaction not agreed)";
    parts.push(s);
  }
  return parts.join(" · ");
}

export type TermChange = { label: string; before?: string; after?: string };

export function diffTerms(prev: Term[] | undefined, next: Term[]): TermChange[] {
  const out: TermChange[] = [];
  const p = new Map((prev ?? []).map((t) => [t.code, t]));
  for (const t of next) {
    const old = p.get(t.code);
    const after = termText(t, next);
    if (!old) out.push({ label: t.label, after });
    else {
      const before = termText(old, prev!);
      out.push({ label: t.label, before: before === after ? undefined : before, after });
    }
    p.delete(t.code);
  }
  for (const t of p.values()) out.push({ label: t.label, before: termText(t, prev!) });
  return out;
}
