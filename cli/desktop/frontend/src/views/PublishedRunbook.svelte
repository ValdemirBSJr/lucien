<script lang="ts">
  import { onMount } from 'svelte';
  import { t } from '../lib/i18n';
  import { closePublished } from '../lib/router';
  import { confirmDialog } from '../lib/confirm';
  import Icon from '../lib/Icon.svelte';
  import { ICON_CLOSE } from '../lib/icons';
  import MarkdownEditor from '../lib/MarkdownEditor.svelte';
  import { GetPublishedContent, ReviseRunbook } from '../../wailsjs/go/main/App';
  import { main } from '../../wailsjs/go/models';

  export let id: string;

  // viewing -> lendo, so leitura. editing -> revisando, markdown editavel.
  // publishing -> enviando a revisao.
  type Phase = 'loading' | 'viewing' | 'editing' | 'publishing';

  let phase: Phase = 'loading';
  let original = '';
  let contentHash = '';
  let markdown = '';
  let assets: main.EditorAsset[] = [];
  let loadError = '';
  let reviseError = '';
  let successMessage = '';

  onMount(load);

  async function load(): Promise<void> {
    phase = 'loading';
    loadError = '';
    try {
      const content = await GetPublishedContent(id);
      original = content.markdown;
      markdown = content.markdown;
      contentHash = content.contentHash;
      phase = 'viewing';
    } catch (error) {
      loadError = `${$t('published_load_error')} (${String(error)})`;
    }
  }

  function startRevision(): void {
    successMessage = '';
    reviseError = '';
    assets = [];
    phase = 'editing';
  }

  function cancelRevision(): void {
    markdown = original;
    assets = [];
    reviseError = '';
    phase = 'viewing';
  }

  async function publish(): Promise<void> {
    // Sem mudança não há sucessor a criar -- publicar uma cópia idêntica só
    // consumiria outro UUID à toa, igual ao `lucien runbook revise` do terminal.
    if (markdown.trim() === original.trim()) {
      reviseError = $t('published_no_changes');
      return;
    }
    const confirmed = await confirmDialog($t('published_revise_confirm'));
    if (!confirmed) return;
    reviseError = '';
    const invalidos = assets.filter(
      (asset) => asset.mediaType !== 'image/png' && asset.mediaType !== 'image/jpeg',
    );
    if (invalidos.length > 0) {
      reviseError = `${$t('published_revise_error')} (invalid asset media_type: ${invalidos
        .map((asset) => `${asset.filename}=${JSON.stringify(asset.mediaType)}`)
        .join('; ')})`;
      return;
    }
    phase = 'publishing';
    try {
      const revised = await ReviseRunbook(id, markdown, contentHash, assets);
      successMessage = $t('published_revise_success', { id: revised.id });
      original = markdown;
      assets = [];
      phase = 'viewing';
      // O Hub recusa revisar de novo por cima desta mesma versão agora que
      // ela foi superada -- recarrega o hash/conteúdo para refletir isso.
      await load();
    } catch (error) {
      // O Hub já diz qual é a versão certa quando recusa por estar superada
      // -- não reformula a mensagem, só mostra.
      reviseError = `${$t('published_revise_error')} (${String(error)})`;
      phase = 'editing';
    }
  }
</script>

<div class="published">
  <div class="header">
    <h1>{$t('published_title')}</h1>
    <button class="close" aria-label={$t('home_new_cancel')} on:click={closePublished}>
      <Icon path={ICON_CLOSE} size={18} />
    </button>
  </div>

  {#if phase === 'loading'}
    <p class="hint">{$t('published_loading')}</p>
    {#if loadError}<p class="message error">{loadError}</p>{/if}
  {:else}
    {#if successMessage}<p class="message success">{successMessage}</p>{/if}
    {#if phase === 'viewing'}
      <textarea class="markdown" value={markdown} rows="24" readonly></textarea>
    {:else}
      <MarkdownEditor
        bind:value={markdown}
        bind:assets
        jobIdForAssets={id}
        disabled={phase === 'publishing'}
      />
    {/if}
    {#if reviseError}<p class="message error">{reviseError}</p>{/if}
    <div class="actions">
      {#if phase === 'viewing'}
        <button class="primary" on:click={startRevision}>{$t('published_revise')}</button>
      {:else if phase === 'editing'}
        <button class="secondary" on:click={cancelRevision}>{$t('published_cancel_revision')}</button>
        <button class="primary" on:click={publish}>{$t('published_submit_revision')}</button>
      {:else if phase === 'publishing'}
        <button class="primary" disabled>{$t('published_publishing')}</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .published {
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

  .markdown {
    flex: 1;
    min-height: 0;
    margin-bottom: 14px;
    padding: 14px;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--page);
    color: var(--ink);
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.6;
    resize: none;
  }

  .markdown:read-only {
    background: var(--surface-soft);
    color: var(--ink-soft);
  }

  .markdown:focus {
    outline: 2px solid var(--blue);
    outline-offset: -1px;
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
