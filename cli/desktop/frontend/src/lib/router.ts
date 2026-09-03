import { writable } from 'svelte/store';
import type { main } from '../../wailsjs/go/models';

type EditorAsset = main.EditorAsset;

export type View = 'home' | 'settings' | 'editor' | 'published';

export const view = writable<View>('home');

// ID do runbook PENDENTE aberto no editor -- só tem sentido quando
// `view` é 'editor'. Fica fora do componente porque a navegação (o botão
// "editar" na tabela) mora em Home.svelte, não no editor em si.
export const editingRunbookId = writable<string | null>(null);

export interface PendingLocalRunbook {
  // Id do registro em disco (`local-runbooks/<id>.json`), gerado pelo Go na
  // primeira gravação. Não é job do Hub e nunca chega lá: serve para retomar
  // o rascunho depois de fechar o app, e para apagá-lo pela lista.
  id: string;
  name: string;
  description: string;
  domainFunction: string;
  draft: string;
  // O texto original, preservado ao lado do rascunho ja montado: e a unica
  // entrada que a SLM do Hub sabe enriquecer, e o rascunho local descarta a
  // estrutura dele. Sem isso, "enriquecer" no editor nao teria o que enviar.
  rawLog: string;
  // Imagens já coladas no editor, para que retomar um rascunho salvo não
  // volte com as referências no texto apontando para anexos que sumiram.
  assets: EditorAsset[];
}

// O que o formulário de "Novo runbook" produz: tudo menos o que só existe
// depois de gravar. O `id` vem do disco e as imagens começam vazias, então
// exigi-los do modal o obrigaria a inventar valores que não são dele.
export type NewRunbookRequest = Omit<PendingLocalRunbook, 'id' | 'assets'>;

// UUID-formato-válido usado como job_id nas referências `assets/<id>/...`
// enquanto o runbook ainda não existe no Hub -- o Hub exige exatamente esse
// formato. Substituído pelo id real do job logo antes de publicar.
export const PENDING_ASSET_JOB_TOKEN = '00000000-0000-0000-0000-000000000000';

// Um "Novo runbook" que ainda não existe no Hub -- o modal só monta o
// rascunho localmente; o job nasce quando o operador publica de dentro do
// editor. Mutuamente exclusivo com `editingRunbookId`: um runbook ou já
// existe no Hub (aberto pela tabela) ou ainda não existe (aberto pelo modal).
export const pendingLocalRunbook = writable<PendingLocalRunbook | null>(null);

export function openEditor(id: string): void {
  editingRunbookId.set(id);
  pendingLocalRunbook.set(null);
  view.set('editor');
}

export function openLocalDraft(pending: PendingLocalRunbook): void {
  editingRunbookId.set(null);
  pendingLocalRunbook.set(pending);
  view.set('editor');
}

export function closeEditor(): void {
  editingRunbookId.set(null);
  pendingLocalRunbook.set(null);
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
