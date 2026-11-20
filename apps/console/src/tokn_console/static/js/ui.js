// Alpine components, registered with Alpine.data as the CSP build requires.
// Loaded after the Alpine CSP bundle and before Alpine starts (deferred classic script).

document.addEventListener("alpine:init", () => {
  window.Alpine.data("navMenu", () => ({
    open: false,
    toggle() {
      this.open = !this.open;
      if (this.open) {
        this.$nextTick(() => this.$refs.panel?.querySelector("a, button")?.focus());
      }
    },
    close() {
      this.open = false;
    },
  }));

  window.Alpine.data("copyButton", () => ({
    copied: false,
    async copy(text) {
      try {
        await navigator.clipboard.writeText(text);
        this.copied = true;
        window.dispatchEvent(
          new CustomEvent("toast", { detail: { kind: "success", message: "Copied" } })
        );
        window.setTimeout(() => (this.copied = false), 2000);
      } catch {
        window.dispatchEvent(
          new CustomEvent("toast", {
            detail: {
              kind: "info",
              message: "Copy failed. Select the text and press Ctrl+C.",
            },
          })
        );
      }
    },
  }));
});
