// Toast notifications. DOM APIs and textContent only — never innerHTML.
// Exposed as a module so net.js can drive it from htmx events.

const MAX_VISIBLE = 3;
const DURATION = { success: 5000, info: 5000, warning: 8000, error: 0 };
const MERGE_WINDOW = 2000;

const recent = new Map();

function container() {
  let node = document.getElementById("toasts");
  if (!node) {
    node = document.createElement("div");
    node.id = "toasts";
    node.className = "toasts";
    node.setAttribute("role", "region");
    node.setAttribute("aria-label", "Notifications");
    document.body.append(node);
  }
  return node;
}

function dismiss(toast) {
  if (!toast.isConnected) return;
  toast.remove();
  if (!container().children.length) return;
}

function trim() {
  const box = container();
  while (box.children.length > MAX_VISIBLE) {
    box.firstElementChild?.remove();
  }
}

export function showToast({ kind = "info", message = "" } = {}) {
  if (!message) return;
  const key = `${kind}:${message}`;
  const now = Date.now();
  const last = recent.get(key);
  if (last && now - last < MERGE_WINDOW) return;
  recent.set(key, now);

  const toast = document.createElement("div");
  toast.className = `toast toast--${kind}`;
  const live = kind === "error" ? "alert" : "status";
  toast.setAttribute("role", live);
  toast.setAttribute("aria-live", live === "alert" ? "assertive" : "polite");

  const text = document.createElement("span");
  text.className = "toast__message";
  text.textContent = message;

  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast__close";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "\u00d7";
  close.addEventListener("click", () => dismiss(toast));

  toast.append(text, close);
  container().append(toast);
  trim();

  toast.addEventListener("mouseenter", () => toast.dataset.paused = "true");
  toast.addEventListener("mouseleave", delete toast.dataset.paused);
  toast.addEventListener("focusin", () => toast.dataset.paused = "true");
  toast.addEventListener("focusout", delete toast.dataset.paused);

  const ttl = DURATION[kind] ?? 5000;
  if (ttl > 0) {
    const timer = setInterval(() => {
      if (toast.dataset.paused === "true") return;
      clearInterval(timer);
      dismiss(toast);
    }, ttl);
  }
  return toast;
}

export function listenForToasts() {
  window.addEventListener("toast", (event) => showToast(event.detail || {}));
}
