import { useEffect, useState } from "react";
import { authRequired, isSignedIn, signIn, signOut } from "./api";
import { HouseholdProvider, useHousehold } from "./household";
import { Activity } from "./views/Activity";
import { Assistant } from "./views/Assistant";
import { BillView } from "./views/BillView";
import { Helpers } from "./views/Helpers";
import { Overview } from "./views/Overview";

function useRoute() {
  const [hash, setHash] = useState(location.hash.slice(1) || "/");
  useEffect(() => {
    const on = () => setHash(location.hash.slice(1) || "/");
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return hash.split("/").filter(Boolean);
}

export default function App() {
  const [signedIn, setSignedIn] = useState(isSignedIn());
  if (!signedIn) return <Login onDone={() => setSignedIn(true)} />;
  return (
    <HouseholdProvider>
      <Shell />
    </HouseholdProvider>
  );
}

function Shell() {
  const route = useRoute();
  const { state, error } = useHousehold();
  const [assistantOpen, setAssistantOpen] = useState(false);
  const pending = state?.proposals.filter((p) => p.status === "pending").length ?? 0;

  let page: React.ReactNode;
  if (route[0] === "bills" && route[1]) page = <BillView key={route[1]} billId={route[1]} />;
  else if (route[0] === "helpers") page = <Helpers helperId={route[1]} />;
  else if (route[0] === "activity") page = <Activity />;
  else page = <Overview />;

  const tab = (path: string, label: React.ReactNode, active: boolean) => (
    <a href={`#${path}`} className={active ? "active" : ""}>
      {label}
    </a>
  );

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/">
          Rivera <span>Helper Pay</span>
        </a>
        <nav aria-label="Main">
          {tab("/", <>Overview{pending > 0 && <span className="count">{pending}</span>}</>, !route[0] || route[0] === "bills")}
          {tab("/helpers", "Helpers", route[0] === "helpers")}
          {tab("/activity", "Activity", route[0] === "activity")}
        </nav>
        <div className="row">
          <button className="btn small assistant-toggle" onClick={() => setAssistantOpen((o) => !o)}>
            {assistantOpen ? "Close assistant" : "Assistant"}
          </button>
          {authRequired() && (
            <button className="btn small ghost" onClick={signOut}>
              Sign out
            </button>
          )}
        </div>
      </header>
      <div className={`body ${assistantOpen ? "assistant-open" : ""}`}>
        <main className="page">
          {error && <p className="error-text">Couldn't load your household: {error}</p>}
          {state ? page : !error && <p className="muted">Loading…</p>}
        </main>
        <Assistant />
      </div>
    </div>
  );
}

function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("jordan");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="login">
      <form
        className="card stack"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await signIn(username, password);
            onDone();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <h1 className="brand">
          Rivera <span>Helper Pay</span>
        </h1>
        <p className="muted small">Sign in to check and approve what you owe your helpers.</p>
        <label className="field">
          <span>Username</span>
          <input id="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">
          <span>Password</span>
          <input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="error-text">{error}</p>}
        <button className="btn primary" disabled={busy || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
