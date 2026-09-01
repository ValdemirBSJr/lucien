<script lang="ts">
  import { onMount } from 'svelte';
  import { t } from './i18n';
  import { GetConnectionSettings, SaveConnectionSettings, PickCAFile } from '../../wailsjs/go/main/App';
  import { refreshSession } from './session';
  import Icon from './Icon.svelte';
  import { ICON_VISIBILITY, ICON_VISIBILITY_OFF } from './icons';

  export let onSaved: (() => void) | undefined = undefined;

  let apiHost = '';
  let caFile = '';
  let saving = false;
  let savedMessage = '';
  let errorMessage = '';

  // O Lucien grava sessoes de terminal e aceita imagem com OCR. Um endereco de
  // Hub visivel na tela e um endereco a menos que pode acabar dentro de um
  // runbook publicado, por captura ou por ombro. Nao e confidencialidade: o
  // desktop-connection.json continua legivel pelo mesmo usuario.
  let revealed = false;

  onMount(async () => {
    try {
      const current = await GetConnectionSettings();
      apiHost = current.apiHost;
      caFile = current.caFile;
    } catch {
      // primeira execucao: formulario comeca vazio, sem erro pra mostrar
    }
    // Nada configurado ainda nao tem o que esconder, e mascarar atrapalharia
    // quem esta digitando pela primeira vez.
    revealed = apiHost === '' && caFile === '';
  });

  // Preserva esquema e porta, que dizem como conectar sem dizer onde:
  // https://hub.exemplo.interno:8443 -> https://******:8443
  function maskHost(value: string): string {
    if (!value) return '';
    const parts = value.match(/^([a-z][a-z0-9+.-]*:\/\/)?([^/:]+)(:\d+)?(.*)$/i);
    if (!parts) return '******';
    return `${parts[1] ?? ''}******${parts[3] ?? ''}${parts[4] ?? ''}`;
  }

  // O caminho carrega o nome da conta de quem instalou -- e o que mais
  // interessa esconder aqui. O nome do arquivo fica, para dizer qual CA e.
  function maskPath(value: string): string {
    if (!value) return '';
    const separator = value.includes('\\') ? '\\' : '/';
    const segments = value.split(/[\\/]/);
    const name = segments[segments.length - 1];
    return segments.length > 1 ? `\u2026${separator}${name}` : value;
  }

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
    <div class="masked-row">
      {#if revealed}
        <input
          type="text"
          bind:value={apiHost}
          placeholder={$t('connection_host_placeholder')}
          required
        />
      {:else}
        <input type="text" value={maskHost(apiHost)} readonly tabindex="-1" />
      {/if}
      <button
        type="button"
        class="reveal"
        title={revealed ? $t('connection_hide') : $t('connection_reveal')}
        aria-label={revealed ? $t('connection_hide') : $t('connection_reveal')}
        aria-pressed={revealed}
        on:click={() => (revealed = !revealed)}
      >
        <Icon path={revealed ? ICON_VISIBILITY_OFF : ICON_VISIBILITY} size={18} />
      </button>
    </div>
  </label>
  <label>
    <span>{$t('connection_ca_label')}</span>
    <div class="file-row">
      <div class="masked-row">
        {#if revealed}
          <input
            type="text"
            bind:value={caFile}
            placeholder={$t('connection_ca_placeholder')}
            required
          />
        {:else}
          <input type="text" value={maskPath(caFile)} readonly tabindex="-1" />
        {/if}
      </div>
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

  /* O olho fica dentro do campo; o padding a direita reserva o espaco para
     que o texto nunca passe por baixo dele. */
  .masked-row {
    position: relative;
    display: flex;
    flex: 1;
    min-width: 0;
  }

  .masked-row input {
    flex: 1;
    min-width: 0;
    padding-right: 40px;
  }

  .reveal {
    position: absolute;
    right: 4px;
    top: 50%;
    transform: translateY(-50%);
    display: flex;
    align-items: center;
    padding: 6px;
    border: 0;
    border-radius: 6px;
    color: var(--ink-soft);
    background: transparent;
    cursor: pointer;
  }

  .reveal:hover {
    color: var(--ink);
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
