import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button, ErrorBox, Field, cx } from "./ui";
import { IconX } from "./icons";

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const first = ref.current?.querySelector<HTMLElement>("textarea, input, select, button[data-autofocus]");
    first?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px]" onClick={onClose} />
      <div
        ref={ref}
        className={cx(
          "relative flex max-h-[90vh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl sm:rounded-2xl",
          wide ? "sm:max-w-3xl" : "sm:max-w-md",
        )}
      >
        <div className="flex items-center justify-between gap-2 border-b border-line px-5 py-3.5">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <button type="button" onClick={onClose} className="rounded p-1 text-muted hover:bg-raised hover:text-ink" aria-label="Close">
            <IconX />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line bg-raised/40 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

/**
 * Asks for a reason (and optional extra fields) and runs a server action. The server's error, e.g. a
 * 422 from a rejected policy write, is shown inline and the dialog stays open.
 */
export function ReasonDialog({
  open,
  onClose,
  title,
  description,
  confirmLabel,
  tone = "primary",
  reasonLabel = "Reason",
  reasonHint = "Logged with your name and shown to the affected employee.",
  reasonRequired = true,
  placeholder,
  children,
  onConfirm,
  canConfirm = true,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: string;
  tone?: "primary" | "danger" | "good";
  reasonLabel?: string;
  reasonHint?: ReactNode;
  reasonRequired?: boolean;
  placeholder?: string;
  children?: ReactNode;
  onConfirm: (reason: string) => Promise<unknown>;
  canConfirm?: boolean;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (open) {
      setReason("");
      setError(null);
      setBusy(false);
    }
  }, [open]);

  const submit = async () => {
    if (reasonRequired && !reason.trim()) {
      setError(new Error("A reason is required."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant={tone} onClick={submit} disabled={busy || !canConfirm || (reasonRequired && !reason.trim())}>
            {busy ? "Working…" : confirmLabel}
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        {description && <div className="text-sm text-ink2">{description}</div>}
        {children}
        <Field label={reasonLabel + (reasonRequired ? "" : " (optional)")} hint={reasonHint}>
          <textarea
            className="input min-h-[76px] resize-y"
            value={reason}
            placeholder={placeholder}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
            }}
          />
        </Field>
        {error != null && <ErrorBox error={error} compact />}
      </form>
    </Dialog>
  );
}
