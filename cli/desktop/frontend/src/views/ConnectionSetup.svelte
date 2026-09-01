<script lang="ts">
  import logo from '../assets/logo-lucien.svg';
  import { t } from '../lib/i18n';
  import ConnectionForm from '../lib/ConnectionForm.svelte';
</script>

<div class="setup">
  <div class="card">
    <img src={logo} alt="Lucien" class="brand-logo logo" />
    <h1>{$t('connection_setup_title')}</h1>
    <p>{$t('connection_setup_lead')}</p>
    <ConnectionForm />
  </div>
</div>

<style>
  .setup {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    padding: 24px;
  }

  .card {
    width: 100%;
    max-width: 420px;
    padding: 32px;
    border: 1px solid var(--line);
    border-radius: 16px;
    background: var(--surface);
    box-shadow: 0 20px 45px rgba(28, 48, 94, 0.09);
  }

  .logo {
    width: 40px;
    height: 40px;
    margin-bottom: 16px;
  }

  h1 {
    margin: 0 0 8px;
    font-size: 18px;
    letter-spacing: -0.02em;
  }

  p {
    margin: 0 0 24px;
    color: var(--ink-soft);
    font-size: 13px;
    line-height: 1.6;
  }
</style>
