<script lang="ts">
  import { tick } from 'svelte';
  import { t } from './i18n';
  import { promptDialog } from './prompt';
  import Icon from './Icon.svelte';
  import {
    ICON_FORMAT_BOLD,
    ICON_TITLE,
    ICON_TABLE,
    ICON_IMAGE,
    ICON_CODE,
    ICON_SUBJECT,
    ICON_QUOTE,
    ICON_POST_ADD,
    ICON_LINK,
  } from './icons';
  import { ImportImage } from '../../wailsjs/go/main/App';
  import { main } from '../../wailsjs/go/models';

  export let value = '';
  export let assets: main.EditorAsset[] = [];
  // Token usado em `assets/<token>/<arquivo>` nas referências inseridas --
  // o id real do job quando ele já existe, ou um placeholder combinado com
  // quem publica, quando o runbook ainda não existe no Hub.
  export let jobIdForAssets: string;
  export let disabled = false;
  export let rows = 24;

  let textareaEl: HTMLTextAreaElement | undefined;
  let imageError = '';

  function focusAndSelect(start: number, end: number): void {
    tick().then(() => {
      textareaEl?.focus();
      textareaEl?.setSelectionRange(start, end);
    });
  }

  function wrapSelection(marker: string): void {
    const el = textareaEl;
    if (!el) return;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const selected = value.slice(start, end);
    value = value.slice(0, start) + marker + selected + marker + value.slice(end);
    focusAndSelect(start + marker.length, start + marker.length + selected.length);
  }

  function insertBold(): void {
    wrapSelection('**');
  }

  function insertHeading(): void {
    const el = textareaEl;
    if (!el) return;
    const start = el.selectionStart;
    const lineStart = value.lastIndexOf('\n', start - 1) + 1;
    const prefix = '## ';
    value = value.slice(0, lineStart) + prefix + value.slice(lineStart);
    focusAndSelect(start + prefix.length, start + prefix.length);
  }

  function insertCodeBlock(language: 'bash' | 'text'): void {
    const el = textareaEl;
    const start = el ? el.selectionStart : value.length;
    const end = el ? el.selectionEnd : value.length;
    const selected = value.slice(start, end);
    const needsLeadingBreak = start > 0 && value[start - 1] !== '\n';
    const opening = `${needsLeadingBreak ? '\n' : ''}\`\`\`${language}\n`;
    const block = `${opening}${selected}\n\`\`\`\n`;
    value = value.slice(0, start) + block + value.slice(end);
    const cursorStart = start + opening.length;
    focusAndSelect(cursorStart, cursorStart + selected.length);
  }

  function insertQuote(): void {
    const el = textareaEl;
    if (!el) return;
    const start = el.selectionStart;
    const lineStart = value.lastIndexOf('\n', start - 1) + 1;
    const prefix = '> ';
    value = value.slice(0, lineStart) + prefix + value.slice(lineStart);
    focusAndSelect(start + prefix.length, start + prefix.length);
  }

  // Numeração automática: acha o maior "### Passo N:"/"### Step N:" já no
  // texto e propõe o próximo -- a gramática do Hub exige sequência a partir
  // de 1, então adivinhar meio a esmo só geraria um número pra corrigir.
  function nextStepNumber(): number {
    const encontrados = [...value.matchAll(/^### (?:Passo|Step) ([1-9][0-9]*):/gm)];
    if (encontrados.length === 0) return 1;
    return Math.max(...encontrados.map((item) => Number(item[1]))) + 1;
  }

  // Sempre logo antes de "## Validação"/"## Validation" -- é onde o
  // procedimento termina de verdade, e é raro o operador estar com o
  // cursor lá quando decide acrescentar mais um passo. Sem essa seção
  // (documento fora do modelo padrão), cai no cursor como antes.
  function procedureEndPosition(): number {
    const match = value.match(/^## (?:Validação|Validation)\b/m);
    return match?.index ?? -1;
  }

  function insertStep(): void {
    const el = textareaEl;
    const procedureEnd = procedureEndPosition();
    const start = procedureEnd >= 0 ? procedureEnd : el ? el.selectionStart : value.length;
    const needsLeadingBreak = start > 0 && value[start - 1] !== '\n';
    // Três `#`, não dois -- é o único nível que o Hub reconhece como início
    // de passo (## viraria um subtítulo comum, e um ```bash logo abaixo
    // seria recusado por não pertencer a passo nenhum).
    // A ação entra preenchida, não vazia. O Hub exige de 1 a 120 caracteres
    // depois dos dois-pontos, e um título terminado em ": " não é reconhecido
    // como passo -- o documento inteiro era recusado com "must contain at
    // least one operational step", sem dizer que o problema estava no título.
    // Deixá-la escrita garante um passo válido mesmo se ninguém a substituir.
    const acao = $t('markdown_step_action');
    const heading = `${needsLeadingBreak ? '\n' : ''}### ${$t('markdown_step_word')} ${nextStepNumber()}: ${acao}\n\n`;
    value = value.slice(0, start) + heading + value.slice(start);
    // Seleciona a ação: digitar a substitui de imediato. Antes o cursor caía
    // na linha em branco abaixo, e o título ficava para trás sem ser notado.
    const acaoStart = start + heading.length - acao.length - 2;
    focusAndSelect(acaoStart, acaoStart + acao.length);
  }

  // Insere `[texto do link]()`. Com trecho selecionado, ele vira o texto e o
  // cursor cai dentro dos parenteses, que e o que falta digitar; sem selecao,
  // o rotulo entra escrito e selecionado, para digitar substituir.
  function insertLink(): void {
    const el = textareaEl;
    const start = el ? el.selectionStart : value.length;
    const end = el ? el.selectionEnd : value.length;
    const selecionado = value.slice(start, end);
    const rotulo = selecionado || $t('markdown_link_text');
    const trecho = `[${rotulo}]()`;
    value = value.slice(0, start) + trecho + value.slice(end);
    if (selecionado) {
      // Entre os parenteses: `[...](` tem o tamanho do rotulo mais tres.
      const url = start + rotulo.length + 3;
      focusAndSelect(url, url);
    } else {
      focusAndSelect(start + 1, start + 1 + rotulo.length);
    }
  }

  function insertTable(): void {
    const el = textareaEl;
    const start = el ? el.selectionStart : value.length;
    const needsLeadingBreak = start > 0 && value[start - 1] !== '\n';
    const template =
      `${needsLeadingBreak ? '\n' : ''}\n` +
      `| ${$t('markdown_table_column')} 1 | ${$t('markdown_table_column')} 2 |\n` +
      '| --- | --- |\n' +
      '|  |  |\n\n';
    value = value.slice(0, start) + template + value.slice(start);
    focusAndSelect(start + template.length, start + template.length);
  }

  function arrayBufferToBase64(buffer: ArrayBuffer): string {
    let binary = '';
    const bytes = new Uint8Array(buffer);
    for (let index = 0; index < bytes.byteLength; index += 1) {
      binary += String.fromCharCode(bytes[index]);
    }
    return btoa(binary);
  }

  async function attachImage(
    contentBase64: string,
    mediaType: string,
    filename: string,
  ): Promise<void> {
    const caption = await promptDialog($t('markdown_image_caption_prompt'));
    if (!caption) return;
    assets = [...assets, { filename, contentBase64, mediaType }];
    const el = textareaEl;
    const start = el ? el.selectionStart : value.length;
    const reference = `![${caption}](assets/${jobIdForAssets}/${filename})\n`;
    value = value.slice(0, start) + reference + value.slice(start);
    focusAndSelect(start + reference.length, start + reference.length);
  }

  async function importImage(): Promise<void> {
    imageError = '';
    try {
      const picked = await ImportImage();
      if (!picked.filename) return; // seletor cancelado
      await attachImage(picked.contentBase64, picked.mediaType, picked.filename);
    } catch (error) {
      imageError = `${$t('markdown_image_error')} (${String(error)})`;
    }
  }

  async function handlePaste(event: ClipboardEvent): Promise<void> {
    const items = event.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      // O tipo precisa ser capturado aqui, síncrono, antes de qualquer
      // await -- o DataTransferItem do evento de colar só é garantidamente
      // válido dentro do próprio ciclo do evento. Reler item.type depois do
      // await abaixo já devolveu "" (o clipboard tinha sido invalidado pelo
      // navegador), fazendo a checagem de tipo passar mas o valor guardado
      // sair vazio -- o bug real por trás do "media_type inválido" relatado.
      const mediaType = item.type;
      if (!mediaType.startsWith('image/')) continue;
      event.preventDefault();
      imageError = '';
      if (mediaType !== 'image/png' && mediaType !== 'image/jpeg') {
        imageError = $t('markdown_image_unsupported');
        return;
      }
      const file = item.getAsFile();
      if (!file) return;
      const buffer = await file.arrayBuffer();
      const base64 = arrayBufferToBase64(buffer);
      const extension = mediaType === 'image/png' ? 'png' : 'jpg';
      const suffix = Math.random().toString(16).slice(2, 8);
      await attachImage(base64, mediaType, `img-${suffix}.${extension}`);
      return;
    }
  }
</script>

<div class="toolbar">
  <button
    type="button"
    class="tool"
    title={$t('markdown_bold')}
    aria-label={$t('markdown_bold')}
    disabled={disabled}
    on:click={insertBold}
  >
    <Icon path={ICON_FORMAT_BOLD} size={16} />
  </button>
  <button
    type="button"
    class="tool"
    title={$t('markdown_heading')}
    aria-label={$t('markdown_heading')}
    disabled={disabled}
    on:click={insertHeading}
  >
    <Icon path={ICON_TITLE} size={16} />
  </button>
  <button
    type="button"
    class="tool"
    title={$t('markdown_table')}
    aria-label={$t('markdown_table')}
    disabled={disabled}
    on:click={insertTable}
  >
    <Icon path={ICON_TABLE} size={16} />
  </button>
  <button
    type="button"
    class="tool"
    title={$t('markdown_quote')}
    aria-label={$t('markdown_quote')}
    disabled={disabled}
    on:click={insertQuote}
  >
    <Icon path={ICON_QUOTE} size={16} />
  </button>
  <button
    type="button"
    class="tool"
    title={$t('markdown_code_bash')}
    aria-label={$t('markdown_code_bash')}
    disabled={disabled}
    on:click={() => insertCodeBlock('bash')}
  >
    <Icon path={ICON_CODE} size={16} />
  </button>
  <button
    type="button"
    class="tool"
    title={$t('markdown_code_text')}
    aria-label={$t('markdown_code_text')}
    disabled={disabled}
    on:click={() => insertCodeBlock('text')}
  >
    <Icon path={ICON_SUBJECT} size={16} />
  </button>
  <button
    type="button"
    class="tool"
    title={$t('markdown_add_step')}
    aria-label={$t('markdown_add_step')}
    disabled={disabled}
    on:click={insertStep}
  >
    <Icon path={ICON_POST_ADD} size={16} />
  </button>
  <button
    type="button"
    class="tool"
    title={$t('markdown_link')}
    aria-label={$t('markdown_link')}
    disabled={disabled}
    on:click={insertLink}
  >
    <Icon path={ICON_LINK} size={16} />
  </button>
  <button
    type="button"
    class="tool"
    title={$t('markdown_import_image')}
    aria-label={$t('markdown_import_image')}
    disabled={disabled}
    on:click={importImage}
  >
    <Icon path={ICON_IMAGE} size={16} />
  </button>
  <span class="hint">{$t('markdown_image_notice')}</span>
</div>

{#if imageError}<p class="message error">{imageError}</p>{/if}

<textarea
  class="markdown"
  bind:this={textareaEl}
  bind:value
  {rows}
  {disabled}
  on:paste={handlePaste}
></textarea>

<style>
  .toolbar {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 8px;
  }

  .tool {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border: 1px solid var(--line);
    border-radius: 8px;
    color: var(--ink-soft);
    background: var(--surface);
    cursor: pointer;
  }

  .tool:hover:not(:disabled) {
    color: var(--blue);
    border-color: #9fb2dd;
  }

  .tool:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .hint {
    margin-left: 8px;
    font-size: 11px;
    color: var(--ink-soft);
  }

  .message.error {
    margin: 0 0 8px;
    font-size: 12px;
    color: var(--danger-text);
  }

  .markdown {
    flex: 1;
    min-height: 0;
    width: 100%;
    margin-bottom: 14px;
    padding: 14px;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--page);
    color: var(--ink);
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.6;
    resize: none;
    box-sizing: border-box;
  }

  .markdown:focus {
    outline: 2px solid var(--blue);
    outline-offset: -1px;
  }

  .markdown:disabled {
    opacity: 0.6;
  }
</style>
