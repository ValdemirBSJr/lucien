import { writable } from 'svelte/store';

interface ConfirmRequest {
  message: string;
  resolve: (value: boolean) => void;
}

// Um pedido de cada vez: o app nunca abre duas confirmações sobrepostas.
export const confirmRequest = writable<ConfirmRequest | null>(null);

// Substitui window.confirm() -- o navegador embutido no Wails mostra o alerta
// nativo do SO, que não tem tema nem idioma do app.
export function confirmDialog(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    confirmRequest.set({ message, resolve });
  });
}
