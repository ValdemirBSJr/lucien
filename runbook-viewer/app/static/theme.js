(() => {
  "use strict";

  const storageKey = "lucien-viewer-theme";
  const root = document.documentElement;
  const stored = window.localStorage.getItem(storageKey);
  if (stored === "light" || stored === "dark") {
    root.dataset.theme = stored;
  }

  const preferredTheme = () => {
    if (root.dataset.theme === "light" || root.dataset.theme === "dark") {
      return root.dataset.theme;
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  };

  const button = document.getElementById("theme-toggle");
  if (!button) return;

  const updateLabel = () => {
    const next = preferredTheme() === "dark" ? "claro" : "escuro";
    button.textContent = `Tema ${next}`;
    button.setAttribute("aria-label", `Ativar tema ${next}`);
  };

  updateLabel();
  button.addEventListener("click", () => {
    const next = preferredTheme() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    window.localStorage.setItem(storageKey, next);
    updateLabel();
  });
})();

