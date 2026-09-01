<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import { t } from './i18n';
  import Icon from './Icon.svelte';
  import { ICON_CLOSE } from './icons';
  import {
    CreateRunbook,
    GenerateTypedLogDraft,
    ListDomainFunctions,
  } from '../../wailsjs/go/main/App';

  const dispatch = createEventDispatcher<{
    created: { id: string; draft: string };
    close: void;
  }>();

  let name = '';
  let rawLog = '';
  let description = '';
  let domainFunction = '';
  let domains: string[] = [];
  let submitting = false;
  let errorMessage = '';

  onMount(async () => {
    try {
      domains = await ListDomainFunctions();
    } catch {
      // Sem a lista, o campo fica so com a opcao padrao -- nao bloqueia o envio.
      domains = [];
    }
  });

  function close(): void {
    dispatch('close');
  }

  function onOverlayClick(event: MouseEvent): void {
    if (event.target === event.currentTarget) close();
  }

  async function submit(): Promise<void> {
    submitting = true;
    errorMessage = '';
    try {
      const trimmedName = name.trim();
      const trimmedDescription = description.trim();
      const created = await CreateRunbook(trimmedName, rawLog, trimmedDescription, domainFunction);
      // Opcional: só monta algo quando o campo tem \@ ou texto solto. Vazio
      // (ou já enviado sem essa sintaxe) devolve "" e o fluxo de sempre segue
      // -- o job aparece PROCESSING na tabela, sem abrir o editor sozinho.
      let draft = '';
      try {
        draft = await GenerateTypedLogDraft(created.id, trimmedName, trimmedDescription, rawLog);
      } catch {
        // Geração local é um bônus, não pode bloquear o envio que já aconteceu.
        draft = '';
      }
      dispatch('created', { id: created.id, draft });
    } catch (error) {
      errorMessage = `${$t('home_new_error')} (${String(error)})`;
    } finally {
      submitting = false;
    }
  }
</script>

<div class="overlay" role="presentation" on:click={onOverlayClick}>
  <div class="card" role="dialog" aria-modal="true" tabindex="-1">
    <div class="header">
      <h2>{$t('home_new_title')}</h2>
      <button class="close" aria-label={$t('home_new_cancel')} on:click={close}>
        <Icon path={ICON_CLOSE} size={16} />
      </button>
    </div>
    <form on:submit|preventDefault={submit}>
      <label>
        <span>{$t('home_new_name_label')}</span>
        <input
          type="text"
          bind:value={name}
          placeholder={$t('home_new_name_placeholder')}
          required
        />
      </label>
      <label>
        <span>{$t('home_new_raw_log_label')}</span>
        <textarea
          bind:value={rawLog}
          placeholder={$t('home_new_raw_log_placeholder')}
          rows="8"
        ></textarea>
        <p class="field-hint">{$t('home_new_raw_log_hint')}</p>
      </label>
      <label>
        <span>{$t('home_new_description_label')}</span>
        <input type="text" bind:value={description} />
      </label>
      {#if domains.length > 0}
        <label>
          <span>{$t('home_new_domain_label')}</span>
          <select bind:value={domainFunction}>
            <option value="">{$t('home_new_domain_default')}</option>
            {#each domains as domain (domain)}
              <option value={domain}>{domain}</option>
            {/each}
          </select>
        </label>
      {/if}
      {#if errorMessage}<p class="message error">{errorMessage}</p>{/if}
      <div class="actions">
        <button type="button" class="cancel" on:click={close}>{$t('home_new_cancel')}</button>
        <button type="submit" class="submit" disabled={submitting || !name.trim()}>
          {$t('home_new_submit')}
        </button>
      </div>
    </form>
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
    padding: 20px;
  }

  .card {
    width: 100%;
    max-width: 480px;
    max-height: 90vh;
    overflow-y: auto;
    padding: 24px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
    box-shadow: 0 25px 60px rgba(9, 15, 29, 0.35);
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }

  h2 {
    margin: 0;
    font-size: 16px;
    letter-spacing: -0.02em;
  }

  .close {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
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

  form {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 12px;
    font-weight: 700;
    color: var(--ink-soft);
    text-align: left;
  }

  .field-hint {
    margin: 0;
    font-size: 11px;
    font-weight: 400;
    color: var(--ink-soft);
  }

  input,
  textarea,
  select {
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 8px;
    font-size: 13px;
    font-family: var(--font-mono);
    color: var(--ink);
    background: var(--page);
  }

  textarea {
    resize: vertical;
    min-height: 120px;
  }

  input:focus,
  textarea:focus,
  select:focus {
    outline: 2px solid var(--blue);
    outline-offset: -1px;
  }

  .message.error {
    margin: 0;
    font-size: 12px;
    color: var(--danger-text);
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
    margin-top: 4px;
  }

  button {
    padding: 9px 18px;
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

  .submit {
    border: 0;
    color: #fff;
    background: var(--blue);
  }

  .submit:hover {
    background: var(--blue-deep);
  }

  .submit:disabled {
    opacity: 0.6;
    cursor: default;
  }
</style>
