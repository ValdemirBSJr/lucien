<script lang="ts">
  import { onMount } from 'svelte';
  import logo from './assets/logo-lucien.svg';
  import { t } from './lib/i18n';
  import { view, editingRunbookId, viewingRunbookId } from './lib/router';
  import { sessionPhase, refreshSession } from './lib/session';
  import Settings from './views/Settings.svelte';
  import Home from './views/Home.svelte';
  import Login from './views/Login.svelte';
  import ConnectionSetup from './views/ConnectionSetup.svelte';
  import Editor from './views/Editor.svelte';
  import PublishedRunbook from './views/PublishedRunbook.svelte';
  import Icon from './lib/Icon.svelte';
  import { ICON_SETTINGS } from './lib/icons';
  import ConfirmModal from './lib/ConfirmModal.svelte';

  onMount(() => {
    refreshSession();
  });
</script>

<main>
  <div class="topbar">
    <div class="brand">
      <img src={logo} alt="Lucien" class="brand-logo" />
      <span class="brand-name">{$t('app_title')}</span>
    </div>
    <button
      class="settings-button"
      title={$t('settings_title')}
      aria-label={$t('settings_title')}
      on:click={() => view.set($view === 'settings' ? 'home' : 'settings')}
    >
      <Icon path={ICON_SETTINGS} size={17} />
    </button>
  </div>

  <div class="content">
    {#if $view === 'settings'}
      <Settings />
    {:else if $sessionPhase === 'needs_connection'}
      <ConnectionSetup />
    {:else if $sessionPhase === 'signed_out'}
      <Login />
    {:else if $view === 'editor' && $editingRunbookId}
      <Editor id={$editingRunbookId} />
    {:else if $view === 'published' && $viewingRunbookId}
      <PublishedRunbook id={$viewingRunbookId} />
    {:else if $sessionPhase === 'signed_in'}
      <Home />
    {/if}
  </div>
</main>

<ConfirmModal />

<style>
  main {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 56px;
    padding: 0 20px;
    border-bottom: 1px solid var(--line);
    background: var(--surface);
    flex: 0 0 auto;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .brand-logo {
    width: 26px;
    height: 26px;
  }

  .brand-name {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: -0.02em;
  }

  .settings-button {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border: 1px solid var(--line);
    border-radius: 50%;
    color: var(--ink-soft);
    background: var(--surface);
    cursor: pointer;
    transition: 0.15s ease;
  }

  .settings-button:hover {
    color: var(--blue);
    border-color: #9fb2dd;
    transform: translateY(-1px);
  }

  .content {
    flex: 1;
    min-height: 0;
    overflow: auto;
    background: var(--paper);
  }
</style>
