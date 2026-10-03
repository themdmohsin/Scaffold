/**
 * Toasts — the app-wide replacement for alert(). Small, auto-dismissing,
 * announced via aria-live so screen readers hear them. One ToastProvider at
 * the router root; any component calls useToast() and fires toasts.
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

export type ToastKind = "success" | "error" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

interface ToastContextValue {
  toast: (text: string, kind?: ToastKind) => void;
}

const ToastContext = createContext<ToastContextValue>({ toast: () => undefined });

export function useToast() {
  return useContext(ToastContext);
}

const TIMEOUTS: Record<ToastKind, number> = { success: 4000, info: 5000, error: 8000 };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const toast = useCallback((text: string, kind: ToastKind = "info") => {
    const id = nextId.current++;
    setToasts((prev) => [...prev.slice(-4), { id, kind, text }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, TIMEOUTS[kind]);
  }, []);

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <span>{t.text}</span>
            <button
              type="button"
              className="toast-close"
              aria-label="Dismiss notification"
              onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
