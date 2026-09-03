<script lang="ts">
  import logo from '../assets/logo-lucien.svg';
  import { t } from '../lib/i18n';
  import { view } from '../lib/router';
  import { Login as loginRequest } from '../../wailsjs/go/main/App';
  import { refreshSession } from '../lib/session';
  import PermanentTokenModal from '../lib/PermanentTokenModal.svelte';

  let token = '';
  let submitting = false;
  let errorMessage = '';
  // So vem preenchido quando um token provisorio acabou de ser trocado.
  let issuedToken = '';

  async function submit(): Promise<void> {
    submitting = true;
    errorMessage = '';
    try {
      const result = await loginRequest(token.trim());
      token = '';
      if (result.issuedToken) {
        // A sessao so avanca quando o modal fechar: trocar de tela agora
        // levaria junto o unico momento em que este token e visivel.
        issuedToken = result.issuedToken;
        return;
      }
      await refreshSession();
    } catch (error) {
      // O detalhe tecnico vem junto -- uma mensagem so generica esconderia
      // justamente o que precisa ser corrigido (host, CA, ou o token em si).
      errorMessage = `${$t('login_error_generic')} (${String(error)})`;
    } finally {
      submitting = false;
    }
  }

  async function tokenSaved(): Promise<void> {
    issuedToken = '';
    await refreshSession();
  }
</script>

<div class="login">
  <div class="card">
    <img src={logo} alt="Lucien" class="brand-logo" />
    <h1>{$t('login_title')}</h1>
    <form on:submit|preventDefault={submit}>
      <label>
        <span>{$t('login_token_label')}</span>
        <input
          type="password"
          autocomplete="off"
          bind:value={token}
          placeholder={$t('login_token_placeholder')}
          required
        />
      </label>
      {#if errorMessage}<p class="message error">{errorMessage}</p>{/if}
      <button class="submit" type="submit" disabled={submitting || !token.trim()}>
        {$t('login_submit')}
      </button>
    </form>
    <button class="link" on:click={() => view.set('settings')}>
      {$t('login_settings_link')}
    </button>
  </div>
</div>

{#if issuedToken}
  <PermanentTokenModal token={issuedToken} on:close={tokenSaved} />
{/if}

<style>
  .login {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    padding: 24px;
  }

  .card {
    width: 100%;
    max-width: 360px;
    padding: 32px;
    border: 1px solid var(--line);
    border-radius: 16px;
    background: var(--surface);
    box-shadow: 0 20px 45px rgba(28, 48, 94, 0.09);
    text-align: center;
  }

  .brand-logo {
    width: 40px;
    height: 40px;
    margin-bottom: 12px;
  }

  h1 {
    margin: 0 0 20px;
    font-size: 18px;
    letter-spacing: -0.02em;
  }

  form {
    display: flex;
    flex-direction: column;
    gap: 14px;
    text-align: left;
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
    background: var(--page);
  }

  input:focus {
    outline: 2px solid var(--blue);
    outline-offset: -1px;
  }

  .submit {
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

  .link {
    margin-top: 18px;
    border: 0;
    background: none;
    color: var(--ink-soft);
    font-size: 12px;
    text-decoration: underline;
    cursor: pointer;
  }

  .link:hover {
    color: var(--blue);
  }
</style>
