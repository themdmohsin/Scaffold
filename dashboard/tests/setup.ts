import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom lacks AbortSignal.timeout (used by lib/api) — polyfill it.
if (typeof AbortSignal.timeout !== "function") {
  AbortSignal.timeout = ((ms: number) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), Math.min(ms, 20));
    return controller.signal;
  }) as typeof AbortSignal.timeout;
}

// jsdom lacks matchMedia (used by lib/theme for prefers-color-scheme).
if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

// Engine calls go through fetch — every test mocks it explicitly.
vi.mock("../src/lib/supabase", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/lib/supabase")>();
  return actual;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
