<script lang="ts">
  import { onMount } from 'svelte';
  import { t } from '../lib/i18n';
  import { closeEditor } from '../lib/router';
  import { confirmDialog } from '../lib/confirm';
  import Icon from '../lib/Icon.svelte';
  import { ICON_CLOSE } from '../lib/icons';
  import {
    GetRunbookDetail,
    GenerateRunbookDraft,
    PublishRunbook,
  } from '../../wailsjs/go/main/App';
  import { main } from '../../wailsjs/go/models';

  export let id: string;

  // Fases da edição: carregando os detalhes -> escolhendo comandos -> texto
  // gerado à mão editável -> enviando. Cada uma esconde a anterior porque
  // regenerar o modelo por cima de uma edição já feita apagaria o trabalho.
  type Phase = 'loading' | 'select' | 'draft' | 'publishing';

  let phase: Phase = 'loading';
  let detail: main.RunbookDetail | null = null;
  let selected: Record<string, boolean> = {};
  let draft = '';
  let loadError = '';
  let generateError = '';
  let publishError = '';

  onMount(load);

  async function load(): Promise<void> {
    phase = 'loading';
    loadError = '';
    try {
      detail = await GetRunbookDetail(id);
      selected = Object.fromEntries(detail.commands.map((command) => [command, true]));
      phase = 'select';
    } catch (error) {
      loadError = `${$t('editor_load_error')} (${String(error)})`;
    }
  }

  function toggle(command: string): void {
    selected = { ...selected, [command]: !selected[command] };
  }

  async function generate(): Promise<void> {
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

  async function publish(): Promise<void> {
    const confirmed = await confirmDialog($t('editor_publish_confirm'));
    if (!confirmed) return;
    publishError = '';
    phase = 'publishing';
    try {
      await PublishRunbook(id, draft);
      closeEditor();
    } catch (error) {
      publishError = `${$t('editor_publish_error')} (${String(error)})`;
      phase = 'draft';
    }
  }

  async function close(): Promise<void> {
    if (phase === 'draft' || phase === 'publishing') {
      const confirmed = await confirmDialog($t('editor_discard_confirm'));
      if (!confirmed) return;
    }
    closeEditor();
  }
</script>

<div class="editor">
  <div class="header">
    <h1>{detail ? detail.name : $t('editor_title')}</h1>
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
  {:else if (phase === 'draft' || phase === 'publishing') && detail}
    <p class="hint">{$t('editor_draft_hint')}</p>
    <textarea
      class="markdown"
      bind:value={draft}
      rows="24"
      disabled={phase === 'publishing'}
    ></textarea>
    {#if publishError}<p class="message error">{publishError}</p>{/if}
    <div class="actions">
      <button
        class="secondary"
        disabled={phase === 'publishing'}
        on:click={() => (phase = 'select')}
      >
        {$t('editor_back_to_selection')}
      </button>
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

  .markdown:focus {
    outline: 2px solid var(--blue);
    outline-offset: -1px;
  }

  .markdown:disabled {
    opacity: 0.6;
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
