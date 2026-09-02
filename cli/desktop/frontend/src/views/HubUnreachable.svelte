<script lang="ts">
  import { t } from '../lib/i18n';
  import { view } from '../lib/router';
  import { refreshSession } from '../lib/session';
  import Icon from '../lib/Icon.svelte';
  import { ICON_REFRESH } from '../lib/icons';

  let retrying = false;

  async function retry(): Promise<void> {
    retrying = true;
    try {
      await refreshSession();
    } finally {
      retrying = false;
    }
  }
</script>

<div class="offline">
  <h1>{$t('offline_title')}</h1>
  <p>{$t('offline_body')}</p>
  <div class="actions">
    <button class="primary" on:click={retry} disabled={retrying}>
      <Icon path={ICON_REFRESH} size={16} />
      {$t('offline_retry')}
    </button>
    <button class="secondary" on:click={() => view.set('settings')}>
      {$t('offline_settings')}
    </button>
  </div>
</div>

<style>
  .offline {
    max-width: 460px;
    margin: 0 auto;
    padding: 64px 24px;
    text-align: center;
  }

  h1 {
    margin: 0 0 12px;
    font-size: 18px;
    font-weight: 700;
  }

  p {
    margin: 0 0 24px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ink-soft);
  }

  .actions {
    display: flex;
    gap: 10px;
    justify-content: center;
  }

  button {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 18px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
  }

  .primary {
    border: 0;
    color: #fff;
    background: var(--blue);
  }

  .primary:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .secondary {
    border: 1px solid var(--line);
    color: var(--ink);
    background: var(--surface);
  }
</style>
