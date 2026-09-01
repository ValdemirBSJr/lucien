<script lang="ts">
  import { confirmRequest } from './confirm';
  import { t } from './i18n';

  function answer(value: boolean): void {
    $confirmRequest?.resolve(value);
    confirmRequest.set(null);
  }

  // So fecha quando o clique comeca no proprio overlay -- clicar dentro do
  // cartao nao deve contar como "fora", mas o cartao nao precisa de um
  // handler de clique so para isso.
  function onOverlayClick(event: MouseEvent): void {
    if (event.target === event.currentTarget) answer(false);
  }
</script>

{#if $confirmRequest}
  <div class="overlay" role="presentation" on:click={onOverlayClick}>
    <div class="card" role="dialog" aria-modal="true" tabindex="-1">
      <p>{$confirmRequest.message}</p>
      <div class="actions">
        <button class="cancel" on:click={() => answer(false)}>{$t('confirm_cancel')}</button>
        <button class="confirm" on:click={() => answer(true)}>{$t('confirm_ok')}</button>
      </div>
    </div>
  </div>
{/if}

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
    max-width: 360px;
    margin: 0 20px;
    padding: 24px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
    box-shadow: 0 25px 60px rgba(9, 15, 29, 0.35);
  }

  p {
    margin: 0 0 20px;
    color: var(--ink);
    font-size: 13px;
    line-height: 1.6;
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

  .confirm:hover {
    background: var(--blue-deep);
  }
</style>
