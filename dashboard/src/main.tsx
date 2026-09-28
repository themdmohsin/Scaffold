import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

// No StrictMode: it double-mounts effects in dev, which opens every Realtime
// channel twice (duplicate subscriptions + duplicate refetches).
createRoot(document.getElementById("root")!).render(<App />);
