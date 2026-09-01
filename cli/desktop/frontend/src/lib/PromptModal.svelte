<script lang="ts">
  import { tick } from 'svelte';
  import { promptRequest } from './prompt';
  import { t } from './i18n';

  let value = '';
  let inputEl: HTMLInputElement | undefined;

  $: if ($promptRequest) {
    value = '';
    // O foco automático é intencional aqui -- é um modal que só existe para
    // capturar este texto, então perder o foco não tira o operador de nada.
    tick().then(() => inputEl?.focus());
  }

  function answer(result: string | null): void {
    $promptRequest?.resolve(result);
    promptRequest.set(null);
  }

  function onOverlayClick(event: MouseEvent): void {
    if (event.target === event.currentTarget) answer(null);
  }

  function onSubmit(): void {
    const trimmed = value.trim();
    if (!trimmed) return;
    answer(trimmed);
  }
</script>

{#if $promptRequest}
  <div class="overlay" role="presentation" on:click={onOverlayClick}>
    <div class="card" role="dialog" aria-modal="true" tabindex="-1">
      <p>{$promptRequest.message}</p>
      <form on:submit|preventDefault={onSubmit}>
        <input
          type="text"
          bind:value
          bind:this={inputEl}
          placeholder={$promptRequest.placeholder}
        />
        <div class="actions">
          <button type="button" class="cancel" on:click={() => answer(null)}>
            {$t('confirm_cancel')}
          </button>
          <button type="submit" class="confirm" disabled={!value.trim()}>
            {$t('confirm_ok')}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    z-index: 110;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(9, 15, 29, 0.45);
  }

  .card {
    width: 100%;
    max-width: 360px;
    margin: 0 20px;
    padding: 24px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
    box-shadow: 0 25px 60px rgba(9, 15, 29, 0.35);
  }

  p {
    margin: 0 0 14px;
    color: var(--ink);
    font-size: 13px;
    line-height: 1.6;
  }

  input {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 8px;
    font-size: 13px;
    color: var(--ink);
    background: var(--page);
    margin-bottom: 16px;
  }

  input:focus {
    outline: 2px solid var(--blue);
    outline-offset: -1px;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }

  button {
    padding: 8px 16px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
  }

  .cancel {
    border: 1px solid var(--line);
    color: var(--ink-soft);
    background: transparent;
  }

  .cancel:hover {
    color: var(--ink);
    border-color: #9fb2dd;
  }

  .confirm {
    border: 0;
    color: #fff;
    background: var(--blue);
  }

  .confirm:hover:not(:disabled) {
    background: var(--blue-deep);
  }

  .confirm:disabled {
    opacity: 0.6;
    cursor: default;
  }
</style>
