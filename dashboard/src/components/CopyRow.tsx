import { useState } from "react";

/** A labelled value with a copy button (used by create-wizard + settings). */
export default function CopyRow({ label, value, note }: { label: string; value: string; note?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy-row">
      <div className="copy-main">
        <span className="copy-label">{label}</span>
        <code className="copy-value">{value}</code>
        {note && <small className="muted">{note}</small>}
      </div>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard
            .writeText(value)
            .then(() => setCopied(true))
            .catch(() => setCopied(false));
        }}
        aria-label={`Copy ${label}`}
      >
        {copied ? "Copied ✓" : "Copy"}
      </button>
    </div>
  );
}
