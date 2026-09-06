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

  const control = document.getElementById("theme-toggle");
  if (!control) return;

  // O rotulo visivel nao muda: quem diz em que posicao o controle esta e o
  // aria-checked, que o leitor de tela anuncia como ligado ou desligado. Um
  // rotulo que se reescreve a cada clique obriga o usuario a deduzir se ele
  // descreve o estado atual ou a proxima acao.
  const refletirEstado = () => {
    control.setAttribute(
      "aria-checked",
      preferredTheme() === "dark" ? "true" : "false",
    );
  };

  refletirEstado();
  control.addEventListener("click", () => {
    const next = preferredTheme() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    window.localStorage.setItem(storageKey, next);
    refletirEstado();
  });
})();
