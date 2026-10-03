/**
 * ConfirmDialog — the app-wide replacement for confirm(). A real dialog
 * element with focus trapping, Escape-to-cancel and a labelled action button,
 * driven imperatively via useConfirm() so call sites read like the old
 * `if (!confirm(...)) return` flow.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

interface ConfirmRequest {
  title: string;
  body?: string;
  confirmLabel?: string;
  danger?: boolean;
  resolve: (ok: boolean) => void;
}

interface ConfirmContextValue {
  confirm: (opts: { title: string; body?: string; confirmLabel?: string; danger?: boolean }) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextValue>({ confirm: async () => true });

export function useConfirm() {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const resolverRef = useRef<((ok: boolean) => void) | null>(null);

  const confirm = useCallback(
    (opts: { title: string; body?: string; confirmLabel?: string; danger?: boolean }) =>
      new Promise<boolean>((resolve) => {
        resolverRef.current = resolve;
        setRequest({ ...opts, resolve });
      }),
    [],
  );

  const settle = useCallback((ok: boolean) => {
    resolverRef.current?.(ok);
    resolverRef.current = null;
    setRequest(null);
  }, []);

  useEffect(() => {
    const dlg = dialogRef.current;
    if (!dlg) return;
    if (request && !dlg.open) dlg.showModal();
    if (!request && dlg.open) dlg.close();
  }, [request]);

  // Escape closes the dialog natively; 'cancel' fires — turn it into resolve(false).
  const onCancel = (e: React.SyntheticEvent) => {
    e.preventDefault();
    settle(false);
  };

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      <dialog ref={dialogRef} className="confirm-dialog" onCancel={onCancel} aria-labelledby="confirm-title">
        {request && (
          <>
            <h3 id="confirm-title">{request.title}</h3>
            {request.body && <p>{request.body}</p>}
            <div className="confirm-actions">
              <button type="button" onClick={() => settle(false)}>
                Cancel
              </button>
              <button
                type="button"
                className={request.danger ? "danger" : "primary"}
                autoFocus
                onClick={() => settle(true)}
              >
                {request.confirmLabel ?? "Confirm"}
              </button>
            </div>
          </>
        )}
      </dialog>
    </ConfirmContext.Provider>
  );
}
