import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, HashRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "./api";
import { AuthProvider } from "./auth";
import { App } from "./App";
import { PreviewBanner } from "./components/PreviewBanner";
import { PREVIEW, freezeClock } from "./lib/preview";
import "./lib/theme";
import "./index.css";

if (PREVIEW) freezeClock();

// The preview is a static page that may be served from any path, so it routes on the URL hash.
const Router = PREVIEW ? HashRouter : BrowserRouter;

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      refetchOnWindowFocus: true,
      retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <Router>
        <AuthProvider>
          <App />
          {PREVIEW && <PreviewBanner />}
        </AuthProvider>
      </Router>
    </QueryClientProvider>
  </StrictMode>,
);
