// API client, auth and shared types.

export type Config = {
  apiUrl: string; // "" = same origin
  auth: null | { region: string; userPoolClientId: string };
};

let config: Config = { apiUrl: "", auth: null };

export async function loadConfig(): Promise<Config> {
  const r = await fetch("/config.json", { cache: "no-store" });
  config = r.ok ? await r.json() : config;
  return config;
}

// ------------------------------------------------------------------ auth

type Tokens = { idToken: string; refreshToken?: string; expiresAt: number };
const TOKEN_KEY = "helperpay.tokens";

function readTokens(): Tokens | null {
  try {
    const raw = localStorage.getItem(TOKEN_KEY);
    return raw ? (JSON.parse(raw) as Tokens) : null;
  } catch {
    return null;
  }
}

function writeTokens(t: Tokens | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, JSON.stringify(t));
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: stay signed in for this tab only */
  }
  memTokens = t;
}

let memTokens: Tokens | null = null;

async function cognito(target: string, body: object) {
  const a = config.auth!;
  const r = await fetch(`https://cognito-idp.${a.region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "content-type": "application/x-amz-json-1.1",
      "x-amz-target": `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.message || "Sign-in failed.");
  return data;
}

export function authRequired() {
  return config.auth !== null;
}

export function isSignedIn() {
  if (!authRequired()) return true;
  const t = memTokens ?? readTokens();
  return !!t && (t.expiresAt > Date.now() || !!t.refreshToken);
}

export async function signIn(username: string, password: string) {
  const data = await cognito("InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH",
    ClientId: config.auth!.userPoolClientId,
    AuthParameters: { USERNAME: username, PASSWORD: password },
  });
  if (!data.AuthenticationResult) throw new Error("This account needs setup we don't support. Check the README.");
  const res = data.AuthenticationResult;
  writeTokens({
    idToken: res.IdToken,
    refreshToken: res.RefreshToken,
    expiresAt: Date.now() + (res.ExpiresIn - 60) * 1000,
  });
}

export function signOut() {
  writeTokens(null);
  location.reload();
}

async function idToken(): Promise<string | null> {
  if (!authRequired()) return null;
  const t = memTokens ?? readTokens();
  if (!t) return null;
  if (t.expiresAt > Date.now()) return t.idToken;
  if (!t.refreshToken) return null;
  const data = await cognito("InitiateAuth", {
    AuthFlow: "REFRESH_TOKEN_AUTH",
    ClientId: config.auth!.userPoolClientId,
    AuthParameters: { REFRESH_TOKEN: t.refreshToken },
  });
  const res = data.AuthenticationResult;
  writeTokens({ idToken: res.IdToken, refreshToken: t.refreshToken, expiresAt: Date.now() + (res.ExpiresIn - 60) * 1000 });
  return res.IdToken;
}

// ------------------------------------------------------------------- http

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const tok = await idToken().catch(() => null);
  if (tok) headers.authorization = `Bearer ${tok}`;
  const r = await fetch(config.apiUrl + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) {
    writeTokens(null);
    location.reload();
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.error || `Request failed (${r.status}).`);
  return data as T;
}

// ------------------------------------------------------------------ types

export type Money = string;

export type Term =
  | { kind: "hourly"; code: string; label: string; base_rate: Money; tiers: { start: string; end: string; rate: Money }[]; day_multipliers: { day: string; multiplier: string; applies_to: "base" | "all" | null }[] }
  | { kind: "package"; code: string; label: string; amount: Money; covers_until: string | null; overflow_term: string | null }
  | { kind: "per_unit"; code: string; label: string; amount: Money; unit: string; duration_minutes: number | null };

export type AgreementVersion = {
  helper_id: string;
  version: number;
  effective_from: string;
  agreed_on: string | null;
  terms: Term[];
  schedule: { days: string[]; description: string; skips_us_holidays: boolean };
  date_notes: { on: string; note: string }[];
  notes: string[];
  sources: { source_id: string; quote: string }[];
  basis: "written" | "recollection";
  confirmed_at: string | null;
};

export type Helper = { helper_id: string; name: string; role: string; payment_method: string; notes: string; versions: AgreementVersion[] };

export type BillSummary = {
  bill_id: string;
  helper_id: string;
  period_start: string;
  period_end: string;
  status: "open" | "approved" | "superseded";
  stated_total: Money | null;
  overall: "matches" | "discrepancies" | "needs_review";
  headline: string;
  expected_total: Money;
  undecided_total: Money;
  approved_amount: Money | null;
  created_at: string;
};

export type Proposal = {
  proposal_id: string;
  kind: "agreement" | "approval";
  status: "pending" | "confirmed" | "rejected";
  summary: string;
  payload: any;
  preview: any;
  created_at: string;
  decided_at?: string;
  reason?: string;
};

export type Source = { source_id: string; kind: "text" | "image"; title: string; created_at: string; text?: string | null };

export type HouseholdState = { helpers: Helper[]; bills: BillSummary[]; proposals: Proposal[]; sources: Source[] };

export type Flag = { level: "info" | "warn" | "error"; message: string };

export type LineCheck = {
  line_no: number;
  raw: string;
  billed: Money;
  expected: Money | null;
  delta: Money | null;
  status: "match" | "overcharge" | "undercharge" | "needs_decision" | "cannot_check" | "ambiguous";
  agreement_version: number | null;
  steps: string[];
  flags: Flag[];
};

export type BillDetail = {
  bill: any;
  check: {
    lines: LineCheck[];
    stated_total: Money | null;
    lines_sum: Money;
    arithmetic_ok: boolean;
    expected_total: Money;
    undecided_total: Money;
    overall: BillSummary["overall"];
    flags: Flag[];
  };
  default_decisions: Record<string, Choice>;
  default_amount: Money;
  undecided: string[];
  source: Source | null;
  versions: AgreementVersion[];
  pending_terms: { proposal_id: string; summary: string; after: Money | null }[];
};

export type Choice = "agreement" | "billed" | "exclude";

export type BillLine = {
  line_no: number;
  raw: string;
  date: string | null;
  start: string | null;
  end: string | null;
  quantity: string;
  unit_price: Money | null;
  amount: Money;
  term_code: string | null;
  stated_weekday: string | null;
  claimed_justification: string;
};

export type Job = {
  job_id: string;
  status: "running" | "done" | "error";
  progress: string[];
  result?: { reply: string; proposals: Proposal[]; bills_recorded: string[]; tools_used: string[] };
  error?: string;
};

export type ChatMessage = { role: "user" | "assistant"; text: string; documents: { source_id: string; title: string }[] };
