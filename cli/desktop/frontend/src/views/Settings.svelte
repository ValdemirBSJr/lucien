<script lang="ts">
  import { theme, type ThemeChoice } from '../lib/theme';
  import { locale, t, type Locale } from '../lib/i18n';
  import { view } from '../lib/router';
  import Icon from '../lib/Icon.svelte';
  import { ICON_LIGHT_MODE, ICON_DARK_MODE, ICON_DESKTOP_WINDOWS } from '../lib/icons';
  import ConnectionForm from '../lib/ConnectionForm.svelte';
  import { sessionPhase, identity, refreshSession } from '../lib/session';
  import { confirmDialog } from '../lib/confirm';
  import { Logout, ForgetEverything, GetAppInfo } from '../../wailsjs/go/main/App';
  import { onMount } from 'svelte';

  async function logout(): Promise<void> {
    if (!(await confirmDialog($t('account_logout_confirm')))) return;
    await Logout();
    await refreshSession();
  }

  async function forgetEverything(): Promise<void> {
    if (!(await confirmDialog($t('account_forget_everything_confirm')))) return;
    await ForgetEverything();
    await refreshSession();
    view.set('home');
  }

  const themeChoices: { value: ThemeChoice; icon: string; labelKey: 'theme_light' | 'theme_dark' | 'theme_system' }[] = [
    { value: 'light', icon: ICON_LIGHT_MODE, labelKey: 'theme_light' },
    { value: 'dark', icon: ICON_DARK_MODE, labelKey: 'theme_dark' },
    { value: 'system', icon: ICON_DESKTOP_WINDOWS, labelKey: 'theme_system' },
  ];

  let appInfo: { productName: string; version: string; copyright: string } | null = null;

  onMount(async () => {
    appInfo = await GetAppInfo();
  });

  function setLocale(value: Locale): void {
    locale.set(value);
  }
</script>

<div class="settings">
  <div class="settings-header">
    <button class="back" on:click={() => view.set('home')}>← {$t('settings_back')}</button>
    <h1>{$t('settings_title')}</h1>
  </div>

  <section>
    <h2>{$t('settings_appearance')}</h2>
    <div class="theme-switch">
      {#each themeChoices as choice}
        <button
          class:active={$theme === choice.value}
          title={$t(choice.labelKey)}
          aria-label={$t(choice.labelKey)}
          on:click={() => theme.set(choice.value)}
        >
          <Icon path={choice.icon} size={20} />
        </button>
      {/each}
    </div>
  </section>

  <section>
    <h2>{$t('settings_language')}</h2>
    <div class="lang-switch">
      <button class:active={$locale === 'pt'} on:click={() => setLocale('pt')}>PT</button>
      <button class:active={$locale === 'en'} on:click={() => setLocale('en')}>EN</button>
    </div>
  </section>

  <section>
    <h2>{$t('settings_connection')}</h2>
    <ConnectionForm />
  </section>

  <section>
    <h2>{$t('settings_account')}</h2>
    {#if $sessionPhase === 'signed_in' && $identity}
      <p class="account-line">{$t('account_signed_in_as')} <strong>{$identity.username}</strong></p>
      <button class="danger-outline" on:click={logout}>{$t('account_logout')}</button>
    {/if}
    <button class="danger" on:click={forgetEverything}>{$t('account_forget_everything')}</button>
  </section>

  {#if appInfo}
    <section class="about">
      <h2>{$t('about_title')}</h2>
      <p class="about-line"><strong>{appInfo.productName}</strong></p>
      <p class="about-line">{appInfo.version}</p>
      <p class="about-line">{appInfo.copyright}</p>
    </section>
  {/if}
</div>

<style>
  .about-line {
    margin: 0 0 4px;
    font-size: 12px;
    color: var(--ink-soft);
  }

  .settings {
    max-width: 560px;
    margin: 0 auto;
    padding: 32px 24px;
  }

  .settings-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 32px;
  }

  .back {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 7px 14px;
    font-size: 12px;
    font-weight: 700;
    color: var(--ink-soft);
    background: var(--surface);
    cursor: pointer;
  }

  .back:hover {
    color: var(--blue);
    border-color: #9fb2dd;
  }

  h1 {
    margin: 0;
    font-size: 20px;
    letter-spacing: -0.02em;
  }

  section {
    margin-bottom: 28px;
    padding-bottom: 28px;
    border-bottom: 1px solid var(--line);
  }

  section:last-child {
    border-bottom: 0;
  }

  h2 {
    margin: 0 0 12px;
    font-size: 13px;
    font-weight: 700;
    color: var(--ink-soft);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .theme-switch,
  .lang-switch {
    display: inline-flex;
    gap: 4px;
    padding: 4px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--surface-soft);
  }

  .theme-switch button {
    display: grid;
    place-items: center;
    width: 40px;
    height: 40px;
    border: 0;
    border-radius: 50%;
    color: var(--ink-soft);
    background: transparent;
    cursor: pointer;
    transition: 0.15s ease;
  }

  .lang-switch button {
    border: 0;
    border-radius: 999px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 700;
    color: var(--ink-soft);
    background: transparent;
    cursor: pointer;
    transition: 0.15s ease;
  }

  .theme-switch button.active,
  .lang-switch button.active {
    color: #fff;
    background: var(--blue);
  }

  .theme-switch button:hover:not(.active),
  .lang-switch button:hover:not(.active) {
    color: var(--blue);
    background: var(--line);
  }

  .account-line {
    margin: 0 0 14px;
    font-size: 13px;
    color: var(--ink-soft);
  }

  .account-line strong {
    color: var(--ink);
  }

  .danger-outline,
  .danger {
    display: block;
    margin-top: 10px;
    padding: 9px 16px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
  }

  .danger-outline {
    border: 1px solid var(--danger-border);
    color: var(--danger-text);
    background: transparent;
  }

  .danger-outline:hover {
    background: var(--danger-bg);
  }

  .danger {
    border: 1px solid var(--danger-border);
    color: var(--danger-text);
    background: var(--danger-bg);
  }

  .danger:hover {
    border-color: var(--danger-text);
  }
</style>
