(() => {
  "use strict";

  // O portal desloga depois de alguns minutos sem uso. Digitar no editor e
  // uso, mas nao gera requisicao: sem isto, quem escreve uma revisao longa
  // perderia a sessao -- e o texto -- ao enviar.
  const editor = document.getElementById("markdown");
  if (!editor) return;

  const intervalo = 60 * 1000;
  let ultimo = Date.now();

  editor.addEventListener("input", () => {
    const agora = Date.now();
    if (agora - ultimo < intervalo) return;
    ultimo = agora;
    window
      .fetch("/session/keepalive", { credentials: "same-origin", cache: "no-store" })
      .catch(() => {});
  });
})();
