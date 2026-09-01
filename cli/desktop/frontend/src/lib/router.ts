import { writable } from 'svelte/store';

export type View = 'home' | 'settings' | 'editor';

export const view = writable<View>('home');

// ID do runbook PENDENTE aberto no editor -- só tem sentido quando
// `view` é 'editor'. Fica fora do componente porque a navegação (o botão
// "editar" na tabela) mora em Home.svelte, não no editor em si.
export const editingRunbookId = writable<string | null>(null);

export function openEditor(id: string): void {
  editingRunbookId.set(id);
  view.set('editor');
}

export function closeEditor(): void {
  editingRunbookId.set(null);
  view.set('home');
}
