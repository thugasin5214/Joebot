import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type ChatMessage, type Job, type Proposal } from "../api";
import { ProposalCard } from "../components";
import { useHousehold } from "../household";

type Attachment = { id: string; title: string; kind: "text" | "image"; text?: string; dataUrl?: string; contentType?: string };
type Turn = ChatMessage & { proposals?: Proposal[]; error?: boolean };
type Session = { session_id: string; title: string; updated_at: string };

const SESSION_KEY = "helperpay.session";
const MAX_IMAGE_BYTES = 3.5 * 1024 * 1024;

function storedSession(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function rememberSession(id: string | null) {
  try {
    if (id) localStorage.setItem(SESSION_KEY, id);
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    /* fine: just won't reopen this conversation next time */
  }
}

async function readImage(file: File): Promise<{ dataUrl: string; contentType: string }> {
  const dataUrl = await new Promise<string>((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result as string);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
  if (file.size <= MAX_IMAGE_BYTES) return { dataUrl, contentType: file.type };
  // Too big to upload as is: re-encode at a readable size.
  const img = new Image();
  await new Promise((res) => {
    img.onload = res;
    img.src = dataUrl;
  });
  const scale = Math.min(1, 2400 / Math.max(img.width, img.height));
  const canvas = document.createElement("canvas");
  canvas.width = img.width * scale;
  canvas.height = img.height * scale;
  canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
  return { dataUrl: canvas.toDataURL("image/jpeg", 0.9), contentType: "image/jpeg" };
}

export function Assistant() {
  const { state, refresh, setAskAssistant } = useHousehold();
  const [sessionId, setSessionId] = useState<string | null>(storedSession);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [progress, setProgress] = useState<string[] | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busy = progress !== null;

  const loadSessions = useCallback(async () => setSessions(await api<Session[]>("GET", "/api/sessions")), []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    if (!sessionId) {
      setTurns([]);
      return;
    }
    api<{ messages: ChatMessage[] }>("GET", `/api/sessions/${sessionId}`)
      .then((r) => setTurns(r.messages))
      .catch(() => {
        setSessionId(null);
        rememberSession(null);
      });
  }, [sessionId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, progress]);

  async function addFiles(files: FileList | File[]) {
    const out: Attachment[] = [];
    for (const f of Array.from(files)) {
      const id = crypto.randomUUID();
      if (f.type.startsWith("image/")) {
        const { dataUrl, contentType } = await readImage(f);
        out.push({ id, title: f.name, kind: "image", dataUrl, contentType });
      } else if (f.type.startsWith("text/") || /\.(txt|eml|md)$/i.test(f.name)) {
        out.push({ id, title: f.name, kind: "text", text: await f.text() });
      } else {
        alertTurn(`I can read screenshots (PNG, JPEG) and text files. “${f.name}” isn't one of those; paste its text instead.`);
      }
    }
    setAttachments((a) => [...a, ...out]);
  }

  function alertTurn(message: string) {
    setTurns((t) => [...t, { role: "assistant", text: message, documents: [], error: true }]);
  }

  function onPaste(e: React.ClipboardEvent) {
    const files = Array.from(e.clipboardData.files);
    if (files.length) {
      e.preventDefault();
      addFiles(files);
      return;
    }
    const pasted = e.clipboardData.getData("text");
    if (pasted.length > 280 || pasted.split("\n").length > 3) {
      // Long pastes are documents: keep them as their own record.
      e.preventDefault();
      const firstLine = pasted.trim().split("\n")[0].slice(0, 50);
      setAttachments((a) => [...a, { id: crypto.randomUUID(), title: firstLine || "Pasted text", kind: "text", text: pasted }]);
    }
  }

  const send = useCallback(
    async (messageText: string, atts: Attachment[]) => {
      if (busy || (!messageText.trim() && !atts.length)) return;
      setProgress(atts.length ? ["Saving your documents"] : ["Thinking"]);
      setTurns((t) => [...t, { role: "user", text: messageText, documents: atts.map((a) => ({ source_id: a.id, title: a.title })) }]);
      setText("");
      setAttachments([]);
      try {
        const sourceIds: string[] = [];
        for (const a of atts) {
          const body =
            a.kind === "image"
              ? { kind: "image", title: a.title, content_type: a.contentType, data_base64: a.dataUrl!.split(",")[1] }
              : { kind: "text", title: a.title, text: a.text };
          sourceIds.push((await api<{ source_id: string }>("POST", "/api/sources", body)).source_id);
        }
        const started = await api<{ job_id: string; session_id: string }>("POST", "/api/chat", {
          session_id: sessionId,
          text: messageText,
          source_ids: sourceIds,
        });
        setSessionId(started.session_id);
        rememberSession(started.session_id);
        let job: Job;
        for (;;) {
          await new Promise((r) => setTimeout(r, 1200));
          job = await api<Job>("GET", `/api/chat/jobs/${started.job_id}`);
          if (job.progress.length) setProgress(job.progress);
          if (job.status !== "running") break;
        }
        if (job.status === "done" && job.result) {
          setTurns((t) => [...t, { role: "assistant", text: job.result!.reply, documents: [], proposals: job.result!.proposals }]);
        } else {
          alertTurn(job.error ?? "Something went wrong. Nothing was changed.");
        }
      } catch (e) {
        alertTurn((e as Error).message);
      } finally {
        setProgress(null);
        refresh();
        loadSessions();
      }
    },
    [busy, sessionId, refresh, loadSessions],
  );

  useEffect(() => {
    setAskAssistant((t: string) => send(t, []));
  }, [send, setAskAssistant]);

  const liveProposal = (p: Proposal) => state?.proposals.find((x) => x.proposal_id === p.proposal_id) ?? p;

  return (
    <aside
      className={`assistant ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        addFiles(e.dataTransfer.files);
      }}
    >
      <header className="assistant-head">
        <h2>Assistant</h2>
        <div className="row">
          <select
            id="session-picker"
            aria-label="Conversation"
            value={sessionId ?? ""}
            onChange={(e) => {
              const v = e.target.value || null;
              setSessionId(v);
              rememberSession(v);
            }}
          >
            <option value="">New conversation</option>
            {sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                {s.title} · {new Date(s.updated_at).toLocaleDateString()}
              </option>
            ))}
          </select>
          {sessionId && (
            <button
              className="btn small"
              onClick={() => {
                setSessionId(null);
                rememberSession(null);
              }}
            >
              New
            </button>
          )}
        </div>
      </header>

      <div className="messages">
        {turns.length === 0 && !busy && (
          <div className="hint stack-sm">
            <p>
              <strong>Drop in what you have.</strong> Screenshots of texts, pasted messages, your own notes, the bills that
              came in.
            </p>
            <p className="small muted">
              I'll draft what you agreed with each helper and check every bill. You confirm anything that changes terms or
              money.
            </p>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`msg ${t.role} ${t.error ? "error" : ""}`}>
            {t.documents.length > 0 && (
              <div className="doc-chips">
                {t.documents.map((d) => (
                  <span key={d.source_id} className="doc-chip">
                    📎 {d.title}
                  </span>
                ))}
              </div>
            )}
            {t.text && (t.role === "assistant" ? <Markdown remarkPlugins={[remarkGfm]}>{t.text}</Markdown> : <p>{t.text}</p>)}
            {t.proposals?.map((p) => (
              <ProposalCard key={p.proposal_id} p={liveProposal(p)} compact />
            ))}
          </div>
        ))}
        {busy && (
          <div className="msg assistant progress" aria-live="polite">
            <ul>
              {progress!.map((p, i) => (
                <li key={p} className={i === progress!.length - 1 ? "current" : "done"}>
                  {p}
                </li>
              ))}
            </ul>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(text, attachments);
        }}
      >
        {attachments.length > 0 && (
          <div className="doc-chips">
            {attachments.map((a) => (
              <span key={a.id} className="doc-chip">
                {a.kind === "image" ? "🖼" : "📄"} {a.title}
                <button type="button" aria-label={`Remove ${a.title}`} onClick={() => setAttachments((x) => x.filter((y) => y.id !== a.id))}>
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <textarea
          id="composer-text"
          rows={3}
          value={text}
          placeholder="Ask, or paste a message or bill…"
          onChange={(e) => setText(e.target.value)}
          onPaste={onPaste}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(text, attachments);
            }
          }}
        />
        <div className="row between">
          <button type="button" className="btn small" onClick={() => fileInput.current?.click()}>
            Attach screenshots or files
          </button>
          <input
            ref={fileInput}
            id="file-input"
            type="file"
            multiple
            accept="image/*,.txt,.eml,.md,text/plain"
            hidden
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <button className="btn primary" disabled={busy || (!text.trim() && !attachments.length)}>
            {busy ? "Working…" : "Send"}
          </button>
        </div>
      </form>
    </aside>
  );
}
