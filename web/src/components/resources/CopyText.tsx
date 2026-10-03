import { useState } from "react";
import { cx } from "../ui";

/** Monospace value (a URN, a handle) with a one-click copy. */
export function CopyText({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard API unavailable (http, old browser): select-and-copy fallback.
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <button
      type="button"
      onClick={copy}
      title={copied ? "Copied" : `Copy ${text}`}
      className={cx(
        "group inline-flex max-w-full items-center gap-1 rounded text-left font-mono text-[11px] text-muted hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
        className,
      )}
    >
      <span className="truncate">{text}</span>
      <span className={cx("shrink-0 text-[10px] font-sans font-medium", copied ? "text-good" : "text-accent opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100")}>
        {copied ? "copied" : "copy"}
      </span>
    </button>
  );
}
