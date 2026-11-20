// Network and feedback behaviour: htmx events, the progress bar, offline handling.
// No inline handlers anywhere — everything is bound here.

import { listenForToasts, showToast } from "./toast.js";

const PROGRESS_DELAY = 150;
const PROGRESS_MIN = 300;

let progressTimer = 0;
let progressShownAt = 0;

function bar() {
  let node = document.getElementById("progress");
  if (!node) {
    node = document.createElement("div");
    node.id = "progress";
    node.className = "progress";
    node.setAttribute("role", "presentation");
    document.body.append(node);
  }
  return node;
}

function startProgress() {
  window.clearTimeout(progressTimer);
  progressTimer = window.setTimeout(() => {
    const node = bar();
    node.dataset.active = "true";
    node.style.width = "70%";
    progressShownAt = Date.now();
  }, PROGRESS_DELAY);
}

function endProgress() {
  window.clearTimeout(progressTimer);
  const node = bar();
  const elapsed = progressShownAt ? Date.now() - progressShownAt : PROGRESS_MIN;
  const wait = Math.max(0, PROGRESS_MIN - elapsed);
  window.setTimeout(() => {
    node.style.width = "100%";
    window.setTimeout(() => {
      node.dataset.active = "false";
      node.style.width = "0";
      progressShownAt = 0;
    }, 120);
  }, wait);
}

function offlineBanner(show) {
  let node = document.getElementById("offline-banner");
  if (!show) {
    node?.remove();
    return;
  }
  if (node) return;
  node = document.createElement("div");
  node.id = "offline-banner";
  node.className = "banner banner--warning";
  node.setAttribute("role", "status");
  node.textContent = "You are offline. Sending is disabled until the connection returns.";
  document.querySelector("main")?.prepend(node);
  document.querySelectorAll("form button[type=submit]").forEach((button) => {
    button.disabled = true;
    button.dataset.offline = "true";
  });
}

function focusNewRegion(event) {
  const target = event.detail.target;
  if (!target) return;
  const focusable = target.querySelector("[autofocus]") || target.querySelector("h1, h2, h3");
  if (focusable) {
    focusable.setAttribute("tabindex", "-1");
    focusable.focus({ preventScroll: true });
  }
}

function describe(status, message) {
  if (status === 429) return "Too many requests. Wait a moment and try again.";
  if (status === 402) return "You do not have enough credit. Top up to continue.";
  if (status === 403) return "That action is not allowed for this account.";
  if (status === 401) return "Your session expired. Sign in again.";
  return message || "Something went wrong on our side. Nothing was charged. Try again.";
}

function handleResponseError(event) {
  const status = event.detail.xhr?.status ?? 0;
  if (status === 401) {
    const next = window.location.pathname + window.location.search;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    return;
  }
  showToast({ kind: "error", message: describe(status) });
}

document.addEventListener("DOMContentLoaded", () => {
  listenForToasts();

  document.body.addEventListener("htmx:beforeRequest", startProgress);
  document.body.addEventListener("htmx:afterRequest", endProgress);
  document.body.addEventListener("htmx:responseError", handleResponseError);
  document.body.addEventListener("htmx:sendError", () => {
    showToast({
      kind: "error",
      message: "Could not reach the server. Nothing was charged. Try again.",
    });
  });
  document.body.addEventListener("htmx:timeout", () => {
    showToast({
      kind: "error",
      message: "That took too long. Nothing was charged. Try again.",
    });
  });
  document.body.addEventListener("htmx:afterSwap", focusNewRegion);

  window.addEventListener("htmx:confirm", (event) => {
    const question = event.detail.elt?.dataset?.confirm;
    if (question && !window.confirm(question)) event.preventDefault();
  });

  window.addEventListener("offline", () => offlineBanner(true));
  window.addEventListener("online", () => {
    offlineBanner(false);
    showToast({ kind: "success", message: "Back online." });
  });

  const initial = document.querySelector("#flash-data");
  if (initial) {
    try {
      const payload = JSON.parse(initial.textContent || "{}");
      if (payload.kind && payload.message) showToast(payload);
    } catch {
      /* a malformed flash is ignored rather than shown */
    }
  }
});
