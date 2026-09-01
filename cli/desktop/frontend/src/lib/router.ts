import { writable } from 'svelte/store';

export type View = 'home' | 'settings' | 'editor' | 'published';

export const view = writable<View>('home');

// ID do runbook PENDENTE aberto no editor -- só tem sentido quando
// `view` é 'editor'. Fica fora do componente porque a navegação (o botão
// "editar" na tabela) mora em Home.svelte, não no editor em si.
export const editingRunbookId = writable<string | null>(null);

// Rascunho já pronto (gerado localmente a partir do \@ no modal de novo
// runbook) -- opcional. Quando presente, o editor pula direto para a fase de
// revisão em vez de esperar o Hub e mostrar a seleção de comandos. Consumido
// uma vez: o editor limpa este valor ao ler, para uma reabertura futura do
// mesmo runbook (pela tabela) não reaproveitar um rascunho velho.
export const editingRunbookDraft = writable<string | null>(null);

export function openEditor(id: string): void {
  editingRunbookId.set(id);
  editingRunbookDraft.set(null);
  view.set('editor');
}

export function openEditorWithDraft(id: string, draft: string): void {
  editingRunbookId.set(id);
  editingRunbookDraft.set(draft);
  view.set('editor');
}

export function closeEditor(): void {
  editingRunbookId.set(null);
  editingRunbookDraft.set(null);
  view.set('home');
}

// ID do runbook PUBLICADO aberto para visualização/revisão -- só tem
// sentido quando `view` é 'published'.
export const viewingRunbookId = writable<string | null>(null);

export function openPublished(id: string): void {
  viewingRunbookId.set(id);
  view.set('published');
}

export function closePublished(): void {
  viewingRunbookId.set(null);
  view.set('home');
}
