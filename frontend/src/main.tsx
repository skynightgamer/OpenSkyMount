import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { start } from "./store";
import "./styles.css";

void start();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Installable app (PWA). Browsers only allow service workers in secure contexts (HTTPS or localhost); over plain
// HTTP on a LAN the page still works, and "Add to Home Screen" still creates a launcher on most phones.
if ("serviceWorker" in navigator && window.isSecureContext && !import.meta.env.DEV) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* not fatal: the app works without it */
    });
  });
}
