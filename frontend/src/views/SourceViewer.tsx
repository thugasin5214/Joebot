import { useEffect, useState } from "react";
import { api, type Source } from "../api";

export function SourceViewer({ source }: { source: Source }) {
  const [img, setImg] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(source.text ?? null);

  useEffect(() => {
    let alive = true;
    api("GET", `/api/sources/${source.source_id}/file`).then((r) => {
      if (!alive) return;
      setText(r.source.text ?? null);
      if (r.data_base64) setImg(`data:${r.source.content_type};base64,${r.data_base64}`);
    });
    return () => {
      alive = false;
    };
  }, [source.source_id]);

  return (
    <section className="card stack-sm">
      <h3>Original: {source.title}</h3>
      <div className="source-grid">
        {img && <img src={img} alt={`Original ${source.title}`} className="source-img" />}
        {text && (
          <div className="stack-sm">
            {source.kind === "image" && <p className="small muted">What the assistant read from it:</p>}
            <pre className="source-text">{text}</pre>
          </div>
        )}
      </div>
    </section>
  );
}
