<script lang="ts">
  import { onMount } from 'svelte';
  import { t } from './i18n';
  import { GetConnectionSettings, SaveConnectionSettings, PickCAFile } from '../../wailsjs/go/main/App';
  import { refreshSession } from './session';

  export let onSaved: (() => void) | undefined = undefined;

  let apiHost = '';
  let caFile = '';
  let saving = false;
  let savedMessage = '';
  let errorMessage = '';

  onMount(async () => {
    try {
      const current = await GetConnectionSettings();
      apiHost = current.apiHost;
      caFile = current.caFile;
    } catch {
      // primeira execucao: formulario comeca vazio, sem erro pra mostrar
    }
  });

  async function pickFile(): Promise<void> {
    try {
      const picked = await PickCAFile();
      if (picked) caFile = picked;
    } catch (error) {
      errorMessage = String(error);
    }
  }

  async function save(): Promise<void> {
    saving = true;
    errorMessage = '';
    savedMessage = '';
    try {
      await SaveConnectionSettings(apiHost.trim(), caFile.trim());
      savedMessage = $t('connection_saved');
      await refreshSession();
      onSaved?.();
    } catch (error) {
      errorMessage = String(error);
    } finally {
      saving = false;
    }
  }
</script>

<form on:submit|preventDefault={save}>
  <label>
    <span>{$t('connection_host_label')}</span>
    <input
      type="text"
      bind:value={apiHost}
      placeholder={$t('connection_host_placeholder')}
      required
    />
  </label>
  <label>
    <span>{$t('connection_ca_label')}</span>
    <div class="file-row">
      <input
        type="text"
        bind:value={caFile}
        placeholder={$t('connection_ca_placeholder')}
        required
      />
      <button type="button" on:click={pickFile}>…</button>
    </div>
  </label>

  {#if errorMessage}<p class="message error">{errorMessage}</p>{/if}
  {#if savedMessage}<p class="message success">{savedMessage}</p>{/if}

  <button class="submit" type="submit" disabled={saving}>{$t('connection_save')}</button>
</form>

<style>
  form {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 12px;
    font-weight: 700;
    color: var(--ink-soft);
  }

  input {
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 8px;
    font-size: 13px;
    font-family: var(--font-mono);
    color: var(--ink);
    background: var(--surface);
  }

  input:focus {
    outline: 2px solid var(--blue);
    outline-offset: -1px;
  }

  .file-row {
    display: flex;
    gap: 8px;
  }

  .file-row input {
    flex: 1;
    min-width: 0;
  }

  .file-row button {
    padding: 0 14px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface);
    cursor: pointer;
  }

  .submit {
    align-self: flex-start;
    padding: 10px 18px;
    border: 0;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 700;
    color: #fff;
    background: var(--blue);
    cursor: pointer;
  }

  .submit:hover {
    background: var(--blue-deep);
  }

  .submit:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .message {
    margin: 0;
    font-size: 12px;
  }

  .message.error {
    color: var(--danger-text);
  }

  .message.success {
    color: var(--success);
  }
</style>
