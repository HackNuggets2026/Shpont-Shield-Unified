import { useState } from "react";
import { SNAPSHOT_AT } from "../lib/preview";
import { IconX } from "./icons";

/** A small note on every page of the static preview: what it is and that it does not write. */
export function PreviewBanner() {
  const [open, setOpen] = useState(true);
  if (!open) return null;
  const when = SNAPSHOT_AT
    ? new Date(SNAPSHOT_AT * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC"
    : null;
  return (
    <div className="fixed inset-x-3 bottom-3 z-50 flex justify-center sm:inset-x-auto sm:right-4">
      <div className="soft-card flex max-w-md items-start gap-3 rounded-2xl px-4 py-3 text-xs text-ink2 shadow-lg">
        <span className="bg-brand mt-0.5 h-2 w-2 shrink-0 rounded-full" />
        <div className="min-w-0">
          <div className="font-semibold text-ink">Read-only preview</div>
          <div className="mt-0.5">
            A seeded 5,000-person bank{when ? `, as it looked on ${when}` : ""}. Buttons that change things are switched off; run the project
            locally for the live version.
          </div>
        </div>
        <button type="button" onClick={() => setOpen(false)} className="shrink-0 rounded p-0.5 text-muted hover:text-ink" aria-label="Hide">
          <IconX size={14} />
        </button>
      </div>
    </div>
  );
}
