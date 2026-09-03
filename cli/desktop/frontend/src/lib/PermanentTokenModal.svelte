<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { ClipboardSetText } from '../../wailsjs/runtime/runtime';
  import { t } from './i18n';

  export let token: string;

  const dispatch = createEventDispatcher<{ close: void }>();

  let copied = false;
  let copyFailed = false;
  let inputEl: HTMLInputElement | undefined;

  // ClipboardSetText e o runtime do Wails, nao navigator.clipboard: o webview
  // roda fora de contexto seguro e a API do navegador falha em silencio ali.
  async function copy(): Promise<void> {
    copyFailed = false;
    try {
      const ok = await ClipboardSetText(token);
      copied = ok;
      copyFailed = !ok;
    } catch {
      copyFailed = true;
    }
    if (copyFailed) {
      // Sem area de transferencia resta selecionar e copiar a mao -- entao
      // pelo menos deixamos o valor ja selecionado.
      inputEl?.select();
    }
  }
</script>

<!-- Sem fechar por clique no overlay nem por Escape, de proposito: fechar sem
     querer aqui custa a credencial, que nao volta a aparecer. -->
<div class="overlay" role="presentation">
  <div class="card" role="dialog" aria-modal="true" aria-labelledby="token-title">
    <h2 id="token-title">{$t('login_permanent_title')}</h2>
    <p class="warning">{$t('login_permanent_warning')}</p>

    <div class="token">
      <input bind:this={inputEl} type="text" readonly value={token} spellcheck="false" />
      <button class="copy" type="button" on:click={copy}>
        {copied ? $t('login_permanent_copied') : $t('login_permanent_copy')}
      </button>
    </div>
    {#if copyFailed}<p class="failed">{$t('login_permanent_copy_failed')}</p>{/if}

    <p class="hint">{$t('login_permanent_hint')}</p>

    <div class="actions">
      <button class="done" type="button" on:click={() => dispatch('close')}>
        {$t('login_permanent_close')}
      </button>
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed;
    inset: 0;
    z-index: 100;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(9, 15, 29, 0.45);
  }

  .card {
    width: 100%;
    max-width: 460px;
    margin: 0 20px;
    padding: 24px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
    box-shadow: 0 25px 60px rgba(9, 15, 29, 0.35);
  }

  h2 {
    margin: 0 0 12px;
    font-size: 15px;
    letter-spacing: -0.01em;
    color: var(--ink);
  }

  .warning {
    margin: 0 0 16px;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--page);
    color: var(--ink);
    font-size: 12px;
    line-height: 1.6;
  }

  .token {
    display: flex;
    gap: 8px;
  }

  input {
    flex: 1;
    min-width: 0;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 8px;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--ink);
    background: var(--page);
  }

  .copy {
    padding: 8px 16px;
    border: 1px solid var(--line);
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    color: var(--ink-soft);
    background: transparent;
    cursor: pointer;
    white-space: nowrap;
  }

  .copy:hover {
    color: var(--ink);
    border-color: #9fb2dd;
  }

  .failed,
  .hint {
    margin: 10px 0 0;
    font-size: 12px;
    line-height: 1.6;
  }

  .failed {
    color: var(--danger-text);
  }

  .hint {
    color: var(--ink-soft);
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    margin-top: 20px;
  }

  .done {
    padding: 8px 16px;
    border: 0;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    color: #fff;
    background: var(--blue);
    cursor: pointer;
  }

  .done:hover {
    background: var(--blue-deep);
  }
</style>
