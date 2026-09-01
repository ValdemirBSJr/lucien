<script lang="ts">
  import { onMount } from 'svelte';
  import { t } from '../lib/i18n';
  import { closeEditor, PENDING_ASSET_JOB_TOKEN, type PendingLocalRunbook } from '../lib/router';
  import { confirmDialog } from '../lib/confirm';
  import Icon from '../lib/Icon.svelte';
  import { ICON_CLOSE } from '../lib/icons';
  import MarkdownEditor from '../lib/MarkdownEditor.svelte';
  import {
    CreateRunbook,
    GetRunbookDetail,
    GenerateRunbookDraft,
    PublishRunbook,
    SaveLocalDraft,
    LoadLocalDraft,
    DeleteLocalDraft,
  } from '../../wailsjs/go/main/App';
  import { main } from '../../wailsjs/go/models';

  // Ou um runbook que já existe no Hub (`id`, aberto pela tabela), ou um
  // rascunho local que ainda não existe lá (`pending`, aberto pelo modal de
  // novo runbook) -- nunca os dois.
  export let id: string | null = null;
  export let pending: PendingLocalRunbook | null = null;

  // Fases da edição: carregando os detalhes -> escolhendo comandos -> texto
  // gerado à mão editável -> enviando. Cada uma esconde a anterior porque
  // regenerar o modelo por cima de uma edição já feita apagaria o trabalho.
  // No modo local (`pending`), começa direto em 'draft': não há job, então
  // não há comando nenhum do Hub para escolher.
  type Phase = 'loading' | 'select' | 'draft' | 'publishing' | 'published';

  let phase: Phase = pending ? 'draft' : 'loading';
  let detail: main.RunbookDetail | null = null;
  let selected: Record<string, boolean> = {};
  let draft = pending ? pending.draft : '';
  let assets: main.EditorAsset[] = [];
  let loadError = '';
  let generateError = '';
  let publishError = '';
  let successMessage = '';

  // Enquanto o job não existe (`pending`), as imagens referenciam um UUID
  // placeholder -- substituído pelo id real logo antes de publicar.
  $: jobIdForAssets = id ?? PENDING_ASSET_JOB_TOKEN;

  onMount(() => {
    if (!pending) void load();
  });

  async function load(): Promise<void> {
    if (!id) return;
    phase = 'loading';
    loadError = '';
    try {
      detail = await GetRunbookDetail(id);
      selected = Object.fromEntries(detail.commands.map((command) => [command, true]));
      // Uma tentativa de publicação anterior pode ter falhado depois do job
      // já existir -- o rascunho salvo localmente (texto + imagens) é mais
      // recente que qualquer coisa que o Hub tenha, então prevalece.
      const saved = await LoadLocalDraft(id);
      if (saved.markdown) {
        draft = saved.markdown;
        assets = saved.assets ?? [];
        phase = 'draft';
      } else {
        phase = 'select';
      }
    } catch (error) {
      loadError = `${$t('editor_load_error')} (${String(error)})`;
    }
  }

  function toggle(command: string): void {
    selected = { ...selected, [command]: !selected[command] };
  }

  async function generate(): Promise<void> {
    if (!id) return;
    generateError = '';
    const chosen = Object.entries(selected)
      .filter(([, checked]) => checked)
      .map(([command]) => command);
    try {
      draft = await GenerateRunbookDraft(id, chosen);
      phase = 'draft';
    } catch (error) {
      generateError = `${$t('editor_generate_error')} (${String(error)})`;
    }
  }

  function delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // Verificação só do cliente, antes de gastar uma ida ao Hub: se algum
  // anexo tiver um media_type diferente dos dois que o Hub aceita, mostra
  // exatamente qual e qual valor -- em vez de descobrir só depois de uma
  // rejeição genérica, ida e volta pela rede.
  function invalidAssetsMessage(list: main.EditorAsset[]): string {
    const invalidos = list.filter(
      (asset) => asset.mediaType !== 'image/png' && asset.mediaType !== 'image/jpeg',
    );
    if (invalidos.length === 0) return '';
    return invalidos
      .map((asset) => `${asset.filename}: media_type=${JSON.stringify(asset.mediaType)}`)
      .join('; ');
  }

  // O job recém-criado nasce PROCESSING; só aceita publicação quando o
  // worker assíncrono do Hub o leva a PENDING. Sem raw_log (este fluxo nunca
  // manda nada pro extrator), essa transição é rápida, mas ainda é uma fila
  // -- espera em vez de tentar publicar contra um job que o Hub ainda recusa.
  async function waitUntilReady(runbookId: string): Promise<void> {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const current = await GetRunbookDetail(runbookId);
      if (current.status === 'PENDING') return;
      if (current.status === 'FAILED') {
        // RunbookDetail não traz o motivo (esse campo só existe na listagem
        // de ativos) -- não deveria acontecer aqui, já que este fluxo nunca
        // manda raw_log pro Hub extrair nada.
        throw new Error('the Hub could not prepare the runbook');
      }
      await delay(1000);
    }
    throw new Error('timed out waiting for the Hub to prepare the runbook');
  }

  async function publishPending(): Promise<void> {
    if (!pending) return;
    const confirmed = await confirmDialog($t('editor_publish_confirm'));
    if (!confirmed) return;
    publishError = '';
    phase = 'publishing';
    // Cada etapa fala com o Hub separadamente -- misturar as três num só
    // catch escondia justamente qual delas estava falhando de verdade.
    let created: main.RunbookRow;
    try {
      // Sempre sem raw_log: o rascunho já foi montado localmente, então não
      // há por que também submeter o texto digitado à extração do Hub -- ela
      // não entende a sintaxe \@ e poderia falhar o job à toa.
      created = await CreateRunbook(pending.name, '', pending.description, pending.domainFunction);
    } catch (error) {
      publishError = `${$t('editor_create_error')} (${String(error)})`;
      phase = 'draft';
      return;
    }
    const finalMarkdown = draft.replaceAll(PENDING_ASSET_JOB_TOKEN, created.id);
    // Salva assim que o job existe de verdade, antes de qualquer outra
    // chamada -- se a espera ou a publicação falharem daqui pra frente,
    // reabrir pela tabela recupera exatamente isto, em vez de uma tela em
    // branco.
    try {
      await SaveLocalDraft(created.id, new main.LocalDraft({ markdown: finalMarkdown, assets }));
    } catch {
      // Preservação local é um bônus; a publicação não pode depender dela.
    }
    try {
      await waitUntilReady(created.id);
    } catch (error) {
      publishError = `${$t('editor_wait_error')} (${String(error)})`;
      phase = 'draft';
      return;
    }
    const problema = invalidAssetsMessage(assets);
    if (problema) {
      publishError = `${$t('editor_publish_error')} (invalid asset media_type: ${problema})`;
      phase = 'draft';
      return;
    }
    try {
      await PublishRunbook(created.id, finalMarkdown, assets);
      await DeleteLocalDraft(created.id).catch(() => {});
      successMessage = $t('editor_publish_success');
      phase = 'published';
    } catch (error) {
      publishError = `${$t('editor_publish_error')} (${String(error)})`;
      phase = 'draft';
    }
  }

  async function publishExisting(): Promise<void> {
    if (!id) return;
    const confirmed = await confirmDialog($t('editor_publish_confirm'));
    if (!confirmed) return;
    publishError = '';
    phase = 'publishing';
    try {
      await SaveLocalDraft(id, new main.LocalDraft({ markdown: draft, assets }));
    } catch {
      // Preservação local é um bônus; a publicação não pode depender dela.
    }
    const problema = invalidAssetsMessage(assets);
    if (problema) {
      publishError = `${$t('editor_publish_error')} (invalid asset media_type: ${problema})`;
      phase = 'draft';
      return;
    }
    try {
      await PublishRunbook(id, draft, assets);
      await DeleteLocalDraft(id).catch(() => {});
      successMessage = $t('editor_publish_success');
      phase = 'published';
    } catch (error) {
      publishError = `${$t('editor_publish_error')} (${String(error)})`;
      phase = 'draft';
    }
  }

  function publish(): Promise<void> {
    return pending ? publishPending() : publishExisting();
  }

  async function close(): Promise<void> {
    if (id && (phase === 'draft' || phase === 'publishing')) {
      // Já existe job real -- fechar preserva o rascunho local (texto e
      // imagens), então não há nada de fato para descartar.
      try {
        await SaveLocalDraft(id, new main.LocalDraft({ markdown: draft, assets }));
      } catch {
        // Preservação local é um bônus; fechar não pode depender dela.
      }
      closeEditor();
      return;
    }
    if (pending && (phase === 'draft' || phase === 'publishing')) {
      const confirmed = await confirmDialog($t('editor_discard_confirm'));
      if (!confirmed) return;
    }
    closeEditor();
  }
</script>

<div class="editor">
  <div class="header">
    <h1>{pending ? pending.name : detail ? detail.name : $t('editor_title')}</h1>
    <button class="close" aria-label={$t('home_new_cancel')} on:click={close}>
      <Icon path={ICON_CLOSE} size={18} />
    </button>
  </div>

  {#if phase === 'loading'}
    <p class="hint">{$t('editor_loading')}</p>
    {#if loadError}<p class="message error">{loadError}</p>{/if}
  {:else if phase === 'select' && detail}
    <p class="hint">{$t('editor_select_hint')}</p>
    {#if detail.commands.length === 0}
      <p class="hint">{$t('editor_no_commands_hint')}</p>
    {:else}
      <ul class="command-list">
        {#each detail.commands as command, index (command + index)}
          <li>
            <label>
              <input type="checkbox" checked={selected[command]} on:change={() => toggle(command)} />
              <code>{command}</code>
            </label>
            {#if detail.commandOutputs[index]}
              <pre class="output">{detail.commandOutputs[index]}</pre>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
    {#if generateError}<p class="message error">{generateError}</p>{/if}
    <div class="actions">
      <button class="primary" on:click={generate}>{$t('editor_generate')}</button>
    </div>
  {:else if phase === 'published'}
    <p class="message success">{successMessage}</p>
    <div class="actions">
      <button class="primary" on:click={closeEditor}>{$t('editor_close')}</button>
    </div>
  {:else if phase === 'draft' || phase === 'publishing'}
    <p class="hint">{$t('editor_draft_hint')}</p>
    <MarkdownEditor
      bind:value={draft}
      bind:assets
      {jobIdForAssets}
      disabled={phase === 'publishing'}
    />
    {#if publishError}<p class="message error">{publishError}</p>{/if}
    <div class="actions">
      {#if id && detail}
        <button
          class="secondary"
          disabled={phase === 'publishing'}
          on:click={() => (phase = 'select')}
        >
          {$t('editor_back_to_selection')}
        </button>
      {/if}
      <button class="primary" disabled={phase === 'publishing' || !draft.trim()} on:click={publish}>
        {phase === 'publishing' ? $t('editor_publishing') : $t('editor_publish')}
      </button>
    </div>
  {/if}
</div>

<style>
  .editor {
    display: flex;
    flex-direction: column;
    height: 100%;
    max-width: 960px;
    margin: 0 auto;
    padding: 24px;
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  h1 {
    margin: 0;
    font-size: 16px;
    letter-spacing: -0.02em;
  }

  .close {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border: 0;
    border-radius: 50%;
    color: var(--ink-soft);
    background: transparent;
    cursor: pointer;
  }

  .close:hover {
    color: var(--ink);
    background: var(--surface-soft);
  }

  .hint {
    margin: 0 0 14px;
    font-size: 12px;
    color: var(--ink-soft);
  }

  .message.error {
    margin: 0 0 14px;
    font-size: 12px;
    color: var(--danger-text);
  }

  .message.success {
    margin: 0 0 14px;
    font-size: 12px;
    color: var(--success);
  }

  .command-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    margin: 0 0 16px;
    padding: 0;
    list-style: none;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--surface);
  }

  .command-list li {
    padding: 10px 14px;
    border-bottom: 1px solid var(--line);
  }

  .command-list li:last-child {
    border-bottom: 0;
  }

  label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    cursor: pointer;
  }

  code {
    font-family: var(--font-mono);
  }

  .output {
    margin: 8px 0 0 24px;
    padding: 8px 10px;
    border-radius: 8px;
    background: var(--surface-soft);
    color: var(--ink-soft);
    font-family: var(--font-mono);
    font-size: 11px;
    white-space: pre-wrap;
    max-height: 120px;
    overflow-y: auto;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }

  button.primary,
  button.secondary {
    padding: 9px 18px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
  }

  .primary {
    border: 0;
    color: #fff;
    background: var(--blue);
  }

  .primary:hover:not(:disabled) {
    background: var(--blue-deep);
  }

  .primary:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .secondary {
    border: 1px solid var(--line);
    color: var(--ink-soft);
    background: transparent;
  }

  .secondary:hover:not(:disabled) {
    color: var(--ink);
    border-color: #9fb2dd;
  }
</style>
