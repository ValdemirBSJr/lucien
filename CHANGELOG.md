# Changelog

Todas as alterações relevantes deste projeto serão registradas neste arquivo.
O formato segue [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) e o
projeto adotará [Semantic Versioning](https://semver.org/) a partir do primeiro
release público.

## [Unreleased]

## [1.1.9] - 2026-08-31

### Security

- o shell gravado por `lucien start` recebe histórico próprio. Antes ele
  compartilhava o `HISTFILE` do operador, e o vazamento era nos dois sentidos:
  uma seta para cima trazia comando de antes da gravação para dentro do runbook,
  e ao encerrar o shell escrevia a sessão inteira no `~/.bash_history`, em texto
  puro, fora do alcance da sanitização do Hub. O histórico do operador não é
  apagado nem alterado;
- a diretiva `gitleaks:allow` deixa de desligar a política de segredos: era
  honrada também no conteúdo submetido, tanto no `lucien job send` quanto no hook
  `pre-receive`.

### Fixed

- qualquer comando do próprio CLI deixa de virar passo do runbook. O filtro
  enumerava `start`, `stop` e `upload`, então `lucien job sent` digitado antes da
  gravação chegava à seleção como se fosse procedimento.

### Changed

- os exemplos da documentação usam shell POSIX, não PowerShell.

## [1.1.8] - 2026-08-28

### Added

- `lucien job cat <id>` imprime o rascunho local de um job pendente ou com erro,
  sem abrir editor e sem consultar o Hub;
- documentação bilingüe: português na raiz e inglês em `/en`, com URLs próprias
  em inglês sem renomear arquivo algum;
- `docs/operacao.md` descreve o que cada regra do scanner de segredos bloqueia.

### Changed

- mensagens de erro do Hub, dos instaladores, do hook `pre-receive` e dos
  scripts de operação passam a ser exibidas em inglês; identificadores,
  variáveis e comentários do código permanecem como estavam;
- os portões de qualidade e os scripts auxiliares ganharam nomes em inglês.

### Fixed

- `lucien stop` e as linhas parciais deixadas pelo autocompletar com Tab não
  entram mais no runbook como comando ou como saída do comando anterior;
- a recusa por política de segredos passa a nomear a regra que casou.

### Security

- a recusa por segredo nomeia a regra, nunca o valor bloqueado;
- as quatro recusas de token provisório voltam a ser indistinguíveis entre si;
- acesso SSH ao jump server endurecido.

## [1.1.7] - 2026-08-24

### Added

- distribuição open source bilíngue;
- providers de publicação local, GitHub e Gitea;
- pacotes nativos do CLI para Linux e macOS, em `amd64` e `arm64`;
- licença Apache 2.0 e política pública de segurança.

### Security

- fixtures públicos usam apenas identidades fictícias e endereços não públicos;
- GitHub Actions são referenciadas por SHA imutável.
