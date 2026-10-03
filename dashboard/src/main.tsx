import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { queryClient } from "./lib/queryClient";
import { AuthProvider } from "./lib/auth";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { ToastProvider } from "./components/Toasts";
import "./index.css";
import "./app-shell.css";

// No StrictMode: it double-mounts effects in dev, which opens every Realtime
// channel twice (duplicate subscriptions + duplicate refetches).
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <ConfirmProvider>
            <App />
          </ConfirmProvider>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  </QueryClientProvider>,
);
