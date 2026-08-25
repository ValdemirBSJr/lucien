# Convenções de tradução — português para inglês americano

Este arquivo orienta a internacionalização das mensagens de `scripts/` e
`deploy/` e a versão inglesa de `docs/`. Fica fora de `docs_dir`, então não
entra no site gerado: é material de quem contribui, não de quem opera.

## O que é traduzido

- Mensagens que chegam ao usuário no terminal: status, erro, aviso, ajuda.
- Textos que o usuário digita em resposta a um prompt (`[y/N]`, `RESTORE`).
- Nomes dos próprios scripts e dos portões de qualidade.
- A documentação em `docs/en`, que espelha `docs/` integralmente.

## O que **não** é traduzido

- Nomes de variável, função, arquivo de configuração e chave de ambiente.
- Comentários de código. Eles explicam por que cada decisão existe e são
  revisados em português; traduzi-los é outra tarefa, com outro risco.
- Banners de login do jump server (`deploy/jump/motd`, `issue.net`).
- Identificadores que o Hub emite ou consome, como `PENDING`, `PUBLISHED`,
  `FAILED`, `adotada`, `aplicada`, `modelo`.

## Como `docs/en` espelha `docs`

O `mkdocs-static-i18n` reconhece duas páginas como a mesma em idiomas
diferentes pelo **caminho idêntico** dentro da pasta do idioma. Então o arquivo
inglês usa o nome do arquivo português, com o conteúdo em inglês:

| Português | Inglês |
| --- | --- |
| `docs/manual-instalacao.md` | `docs/en/manual-instalacao.md` |
| `docs/tutorial.md` | `docs/en/tutorial.md` |
| `docs/operacao.md` | `docs/en/operacao.md` |

Nome de arquivo em português numa pasta inglesa é estranho à primeira vista,
mas é o que faz o seletor de idioma trocar entre as duas versões da **mesma**
página. Renomear os arquivos portugueses quebraria as URLs que já existem.

O português fica na raiz do site (`/tutorial/`) e o inglês em `/en/`
(`/en/tutorial/`). Enquanto o espelho não está completo, `fallback_to_default`
faz a página que falta cair na versão portuguesa em vez de sumir do site.

Nada disso alcança os runbooks publicados: eles são construídos por
`wiki-builder/mkdocs.yml`, que não carrega o plugin.

## Glossário

A coluna de ocorrências vem da contagem nas mensagens atuais de `scripts/` e
`deploy/`, para que os termos mais frequentes sejam decididos primeiro.

| Português | Inglês | Ocorrências | Nota |
| --- | --- | --- | --- |
| Hub | Hub | 86 | Nome próprio do componente; não traduz |
| jump server | jump server | 31 | Já é termo inglês em uso |
| runbook | runbook | 25 | Nome do artefato; não traduz |
| imagem | image | 20 | Imagem de contêiner |
| segredo | secret | 19 | |
| cópia de segurança / backup | backup | 18 | Unificar em `backup` |
| credencial | credential | 10 | |
| restauração | restore | 9 | Verbo e substantivo |
| publicação | publication | 8 | O ato; o resultado é `published runbook` |
| sessão | session | 6 | Sessão de terminal gravada |
| segmentação (de rede) | network segmentation | 5 | |
| volume | volume | 4 | Volume Docker |
| portão | gate | 4 | Portão de qualidade |
| operador | operator | 4 | Quem executa a manobra |
| migração | migration | 3 | Migração de esquema |
| implantação | deployment | 3 | |
| contêiner | container | 2 | |
| captura | capture | 2 | |
| varredura | scan | 1 | O serviço é `secret scanner` |
| revisão | revision | 1 | Versão nova; `review` é o ato de revisar |
| fila | queue | 1 | |
| domínio / área | area | 1 | A variável segue `domain_function`, sem tradução |

### Termos do domínio que ainda não aparecem em mensagens

Entram na tradução de `docs/en` e ficam fixados desde já.

| Português | Inglês | Nota |
| --- | --- | --- |
| rascunho | draft | O Markdown antes da publicação |
| linhagem | lineage | A cadeia de versões de um runbook |
| criticidade | criticality | `baixa/média/alta` → `low/medium/high` |
| sanitização | sanitization | Remoção de segredo antes de o documento existir |
| trilha de auditoria | audit trail | |
| manobra | procedure | "Maneuver" não é usado nesse sentido em inglês |
| equipamento (de rede) | network device | |
| provisória (credencial) | provisional | |
| enriquecimento | enrichment | A etapa do SLM |
| identidade congelada | frozen identity | A autoria gravada na publicação |

## Registro e voz

- Inglês americano: `behavior`, `analyze`, `license` (substantivo e verbo).
- Imperativo direto nas instruções: "Run", "Check", não "You should run".
- Mensagem de erro descreve o que houve, não culpa o usuário:
  `backup file is unreadable`, não `you gave an invalid file`.
- Sem ponto final em mensagem de uma linha; com ponto em parágrafo.
- Preservar o tom do original: onde o português explica a razão de uma recusa,
  o inglês também explica.
