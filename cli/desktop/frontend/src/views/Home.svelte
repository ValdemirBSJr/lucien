<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { t } from '../lib/i18n';
  import { confirmDialog } from '../lib/confirm';
  import Icon from '../lib/Icon.svelte';
  import { ICON_ADD, ICON_DELETE, ICON_EDIT, ICON_REFRESH } from '../lib/icons';
  import {
    openEditor,
    openLocalDraft,
    openPublished,
    type NewRunbookRequest,
  } from '../lib/router';
  import {
    ListActiveRunbooks,
    ListLocalRunbooks,
    ListPublishedMine,
    LoadLocalRunbook,
    SaveLocalRunbook,
    DeleteLocalRunbook,
    DeleteRunbook,
    RetryRunbook,
  } from '../../wailsjs/go/main/App';
  import { main } from '../../wailsjs/go/models';
  import NewRunbookModal from '../lib/NewRunbookModal.svelte';

  type Tab = 'active' | 'published';

  // Uma linha da aba Ativos, venha ela do Hub ou do disco. `local` é o que
  // decide as ações: um rascunho ainda não existe no Hub, então não há o que
  // reenviar nem o que cancelar lá.
  interface ActiveRow {
    id: string;
    name: string;
    status: string;
    createdAt: string;
    processingError: string;
    local: boolean;
  }

  let tab: Tab = 'active';
  let runbooks: ActiveRow[] = [];
  let published: main.PublishedRunbookSummary[] = [];
  let publishedFilter = '';
  let loading = true;
  let loadError = '';
  let showNewRunbook = false;
  // ID em ação (retry/delete) para desabilitar só o botão daquela linha,
  // sem travar a tabela inteira enquanto uma requisição está em voo.
  let busyId = '';

  let pollHandle: ReturnType<typeof setInterval> | undefined;

  const LOCAL_STATUS = 'LOCAL';

  // Os rascunhos são lidos do disco ANTES do Hub, e um erro de rede não os
  // apaga da tela: eles são justamente o que continua acessível com o Hub
  // fora do ar. Sem esta separação, ficar sem rede esconderia trabalho que
  // está a salvo no disco.
  async function loadActive(): Promise<void> {
    let locais: ActiveRow[] = [];
    try {
      locais = (await ListLocalRunbooks()).map((rascunho) => ({
        id: rascunho.id,
        name: rascunho.name,
        status: LOCAL_STATUS,
        createdAt: rascunho.createdAt,
        processingError: '',
        local: true,
      }));
    } catch (error) {
      loadError = `${$t('home_load_error')} (${String(error)})`;
    }
    try {
      const doHub: ActiveRow[] = (await ListActiveRunbooks()).map((row) => ({
        id: row.id,
        name: row.name,
        status: row.status,
        createdAt: row.createdAt,
        processingError: row.processingError,
        local: false,
      }));
      runbooks = [...locais, ...doHub].sort((a, b) =>
        b.createdAt.localeCompare(a.createdAt),
      );
      loadError = '';
    } catch (error) {
      runbooks = locais;
      loadError = `${$t('home_load_error')} (${String(error)})`;
    } finally {
      loading = false;
    }
  }

  async function loadPublished(): Promise<void> {
    try {
      published = await ListPublishedMine();
      loadError = '';
    } catch (error) {
      loadError = `${$t('home_load_error')} (${String(error)})`;
    } finally {
      loading = false;
    }
  }

  async function load(): Promise<void> {
    loading = runbooks.length === 0 && published.length === 0;
    if (tab === 'active') await loadActive();
    else await loadPublished();
  }

  function switchTab(next: Tab): void {
    tab = next;
    void load();
  }

  async function retry(row: ActiveRow): Promise<void> {
    busyId = row.id;
    try {
      await RetryRunbook(row.id);
      await loadActive();
    } catch (error) {
      loadError = `${$t('home_load_error')} (${String(error)})`;
    } finally {
      busyId = '';
    }
  }

  async function remove(row: ActiveRow): Promise<void> {
    const key = row.local
      ? 'home_delete_local_confirm'
      : row.status === 'PROCESSING'
        ? 'home_delete_processing_confirm'
        : 'home_delete_confirm';
    const confirmed = await confirmDialog($t(key, { name: row.name }));
    if (!confirmed) return;
    busyId = row.id;
    try {
      if (row.local) await DeleteLocalRunbook(row.id);
      else await DeleteRunbook(row.id, row.status === 'PROCESSING');
      await loadActive();
    } catch (error) {
      loadError = `${$t('home_load_error')} (${String(error)})`;
    } finally {
      busyId = '';
    }
  }

  // Retomar um rascunho relê do disco em vez de usar o que está na tabela:
  // a listagem carrega os registros inteiros, mas depender disso amarraria o
  // editor à forma da lista -- e a releitura é o mesmo caminho de quem abre o
  // app do zero e clica em editar.
  async function resumeLocal(row: ActiveRow): Promise<void> {
    busyId = row.id;
    try {
      const rascunho = await LoadLocalRunbook(row.id);
      openLocalDraft({
        id: rascunho.id,
        name: rascunho.name,
        description: rascunho.description,
        domainFunction: rascunho.domainFunction,
        draft: rascunho.markdown,
        rawLog: rascunho.rawLog,
        assets: rascunho.assets ?? [],
      });
    } catch (error) {
      loadError = `${$t('home_load_error')} (${String(error)})`;
    } finally {
      busyId = '';
    }
  }

  // O tipo vem de PendingLocalRunbook em vez de ser repetido aqui: escrito a
  // mao, ele saiu de sincronia assim que o modal passou a mandar o texto
  // original junto.
  // "Começar" nunca criou nada no Hub. Agora ele grava o rascunho em disco
  // antes de abrir o editor: assim o runbook já nasce recuperável, e não a
  // partir do primeiro salvamento lá dentro.
  async function onCreated(event: CustomEvent<NewRunbookRequest>): Promise<void> {
    showNewRunbook = false;
    const pendente = event.detail;
    try {
      const salvo = await SaveLocalRunbook(
        new main.LocalRunbook({
          name: pendente.name,
          description: pendente.description,
          domainFunction: pendente.domainFunction,
          rawLog: pendente.rawLog,
          markdown: pendente.draft,
          assets: [],
        }),
      );
      openLocalDraft({ ...pendente, id: salvo.id, assets: [] });
    } catch (error) {
      // Não poder gravar em disco não pode impedir de trabalhar: abre o
      // editor mesmo assim, sem id -- é o comportamento que existia antes
      // desta tela guardar rascunho.
      loadError = `${$t('home_load_error')} (${String(error)})`;
      openLocalDraft({ ...pendente, id: '', assets: [] });
    }
  }

  function statusLabelKey(
    status: string,
  ): 'home_status_pending' | 'home_status_processing' | 'home_status_failed' | 'home_status_local' {
    if (status === LOCAL_STATUS) return 'home_status_local';
    if (status === 'PENDING') return 'home_status_pending';
    if (status === 'FAILED') return 'home_status_failed';
    return 'home_status_processing';
  }

  function statusClass(status: string): string {
    if (status === LOCAL_STATUS) return 'badge neutral';
    if (status === 'PENDING') return 'badge info';
    if (status === 'FAILED') return 'badge danger';
    return 'badge warning';
  }

  $: filteredPublished = published.filter((item) => {
    const needle = publishedFilter.trim().toLowerCase();
    return (
      item.id.toLowerCase().includes(needle) || item.name.toLowerCase().includes(needle)
    );
  });

  function formatDate(iso: string): string {
    const parsed = new Date(iso);
    return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
  }

  onMount(() => {
    void load();
    // PROCESSING é transitório -- sem isso, a linha só sairia do estado
    // desabilitado quando o operador atualizasse a página manualmente.
    pollHandle = setInterval(() => {
      if (tab === 'active') void loadActive();
    }, 5000);
  });

  onDestroy(() => {
    if (pollHandle) clearInterval(pollHandle);
  });
</script>

<div class="home">
  <div class="toolbar">
    <div class="tabs">
      <button class:active={tab === 'active'} on:click={() => switchTab('active')}>
        {$t('home_tab_active')}
      </button>
      <button class:active={tab === 'published'} on:click={() => switchTab('published')}>
        {$t('home_tab_published')}
      </button>
    </div>
    <div class="toolbar-actions">
      <button class="icon-button" title={$t('home_refresh')} aria-label={$t('home_refresh')} on:click={load}>
        <Icon path={ICON_REFRESH} size={17} />
      </button>
      {#if tab === 'active'}
        <button class="primary" on:click={() => (showNewRunbook = true)}>
          <Icon path={ICON_ADD} size={16} />
          {$t('home_new_runbook')}
        </button>
      {/if}
    </div>
  </div>

  {#if loadError}<p class="message error">{loadError}</p>{/if}

  {#if !loading}
    {#if tab === 'active'}
      {#if runbooks.length === 0}
        <p class="empty">{$t('home_empty_active')}</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th>{$t('home_column_name')}</th>
              <th>{$t('home_column_status')}</th>
              <th>{$t('home_column_created')}</th>
              <th>{$t('home_column_actions')}</th>
            </tr>
          </thead>
          <tbody>
            {#each runbooks as row (row.id)}
              <tr class:processing={row.status === 'PROCESSING'}>
                <td>
                  <div class="name">{row.name}</div>
                  {#if row.status === 'FAILED' && row.processingError}
                    <div class="row-error">{row.processingError}</div>
                  {/if}
                </td>
                <td><span class={statusClass(row.status)}>{$t(statusLabelKey(row.status))}</span></td>
                <td class="created">{formatDate(row.createdAt)}</td>
                <td class="actions">
                  {#if row.local}
                    <button
                      class="icon-button"
                      title={$t('home_action_resume')}
                      aria-label={$t('home_action_resume')}
                      disabled={busyId === row.id}
                      on:click={() => resumeLocal(row)}
                    >
                      <Icon path={ICON_EDIT} size={16} />
                    </button>
                  {:else if row.status === 'PENDING'}
                    <button
                      class="icon-button"
                      title={$t('home_action_edit')}
                      aria-label={$t('home_action_edit')}
                      on:click={() => openEditor(row.id)}
                    >
                      <Icon path={ICON_EDIT} size={16} />
                    </button>
                  {/if}
                  {#if !row.local && row.status === 'FAILED'}
                    <button
                      class="icon-button"
                      title={$t('home_action_retry')}
                      aria-label={$t('home_action_retry')}
                      disabled={busyId === row.id}
                      on:click={() => retry(row)}
                    >
                      <Icon path={ICON_REFRESH} size={16} />
                    </button>
                  {/if}
                  <button
                    class="icon-button danger"
                    title={$t('home_action_delete')}
                    aria-label={$t('home_action_delete')}
                    disabled={busyId === row.id}
                    on:click={() => remove(row)}
                  >
                    <Icon path={ICON_DELETE} size={16} />
                  </button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {:else}
      <input
        type="search"
        class="published-search"
        bind:value={publishedFilter}
        placeholder={$t('home_published_search_placeholder')}
      />
      {#if published.length === 0}
        <p class="empty">{$t('home_empty_published')}</p>
      {:else if filteredPublished.length === 0}
        <p class="empty">{$t('home_published_search_empty')}</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th>{$t('home_column_name')}</th>
              <th>UUID</th>
            </tr>
          </thead>
          <tbody>
            {#each filteredPublished as item (item.id)}
              <tr
                class="clickable"
                role="button"
                tabindex="0"
                on:click={() => openPublished(item.id)}
                on:keydown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') openPublished(item.id);
                }}
              >
                <td class="name">{item.name || '—'}</td>
                <td class="published-uuid">{item.id}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {/if}
  {/if}
</div>

{#if showNewRunbook}
  <NewRunbookModal on:created={onCreated} on:close={() => (showNewRunbook = false)} />
{/if}

<style>
  .home {
    padding: 24px;
    max-width: 960px;
    margin: 0 auto;
  }

  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
    gap: 12px;
  }

  .tabs {
    display: flex;
    gap: 6px;
    padding: 4px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--surface);
  }

  .tabs button {
    padding: 6px 16px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--ink-soft);
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
  }

  .tabs button.active {
    color: #fff;
    background: var(--blue);
  }

  .toolbar-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .icon-button {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border: 1px solid var(--line);
    border-radius: 50%;
    color: var(--ink-soft);
    background: var(--surface);
    cursor: pointer;
    transition: 0.15s ease;
  }

  .icon-button:hover:not(:disabled) {
    color: var(--blue);
    border-color: #9fb2dd;
  }

  .icon-button.danger:hover:not(:disabled) {
    color: var(--danger-text);
    border-color: var(--danger-border);
  }

  .icon-button:disabled {
    opacity: 0.4;
    cursor: default;
  }

  .primary {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: 0;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    color: #fff;
    background: var(--blue);
    cursor: pointer;
  }

  .primary:hover {
    background: var(--blue-deep);
  }

  .message.error {
    margin: 0 0 14px;
    font-size: 12px;
    color: var(--danger-text);
  }

  .empty {
    padding: 40px 0;
    color: var(--ink-soft);
    font-size: 13px;
    text-align: center;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
    background: var(--surface);
  }

  thead th {
    padding: 10px 14px;
    text-align: left;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    color: var(--ink-soft);
    background: var(--surface-soft);
    border-bottom: 1px solid var(--line);
  }

  tbody tr {
    border-bottom: 1px solid var(--line);
  }

  tbody tr:last-child {
    border-bottom: 0;
  }

  tbody tr.processing {
    opacity: 0.6;
  }

  tbody tr.clickable {
    cursor: pointer;
  }

  tbody tr.clickable:hover {
    background: var(--surface-soft);
  }

  tbody tr.clickable:focus-visible {
    outline: 2px solid var(--blue);
    outline-offset: -2px;
  }

  td {
    padding: 12px 14px;
    font-size: 13px;
    vertical-align: middle;
  }

  .name {
    font-weight: 600;
  }

  .row-error {
    margin-top: 2px;
    font-size: 11px;
    color: var(--danger-text);
  }

  .created {
    color: var(--ink-soft);
    font-size: 12px;
    white-space: nowrap;
  }

  .actions {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
  }

  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    border: 1px solid transparent;
  }

  .badge.info {
    color: var(--info-text);
    background: var(--info-bg);
    border-color: var(--info-border);
  }

  .badge.warning {
    color: var(--warning-text);
    background: var(--warning-bg);
    border-color: var(--warning-border);
  }

  .badge.danger {
    color: var(--danger-text);
    background: var(--danger-bg);
    border-color: var(--danger-border);
  }

  /* Rascunho local: cinza de propósito. Os outros três estados são do Hub e
     pedem atenção; este é só "ainda comigo", e colori-lo como os demais o
     faria disputar o olho com quem de fato está esperando algo. */
  .badge.neutral {
    color: var(--ink-soft);
    background: var(--page);
    border-color: var(--line);
  }

  .published-search {
    width: 100%;
    margin-bottom: 14px;
    padding: 10px 14px;
    border: 1px solid var(--line);
    border-radius: 999px;
    font-size: 13px;
    color: var(--ink);
    background: var(--surface);
  }

  .published-search:focus {
    outline: 2px solid var(--blue);
    outline-offset: -1px;
  }

  .published-uuid {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--ink-soft);
  }
</style>
