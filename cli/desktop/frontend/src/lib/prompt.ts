import { writable } from 'svelte/store';

interface PromptRequest {
  message: string;
  placeholder: string;
  resolve: (value: string | null) => void;
}

// Um pedido de cada vez, igual ao confirm.ts.
export const promptRequest = writable<PromptRequest | null>(null);

// Substitui window.prompt() -- usado para a legenda obrigatória de cada
// imagem inserida no editor. Devolve null quando o operador cancela.
export function promptDialog(message: string, placeholder = ''): Promise<string | null> {
  return new Promise((resolve) => {
    promptRequest.set({ message, placeholder, resolve });
  });
}
