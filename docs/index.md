# Lucien Runbooks

**Português (Brasil)** · [English](en/index.md)

Esta wiki reúne procedimentos operacionais capturados pelo Lucien, filtrados por um
SLM e obrigatoriamente revisados por uma pessoa antes da publicação.

## Comece aqui

- [Manual de instalação](manual-instalacao.md): instale Hub e CLI, execute o
  bootstrap e consulte todos os comandos do cliente.
- [Tutorial de uso](tutorial.md): execute o primeiro fluxo de ponta a ponta.
- [Implantação isolada e TLS](implantacao-isolada.md): execute somente o CLI ou
  somente a API e gere os certificados necessários.
- [Documentação técnica](documentacao-tecnica.md): arquitetura, API, configuração
  e limitações.
- [Operação e segurança](operacao.md): padrão obrigatório dos runbooks.
- [IAM, RBAC e metadados](iam-rbac.md): identidade e autorização.
- [Publicação da wiki](publicacao.md): portal local, GitHub Pages e dois modos Gitea.

## Fluxo de publicação

1. O operador grava uma sessão com `lucien start <nome> -d "descrição curta"`.
   A descrição é opcional, mas recomendada para melhorar o contexto da SLM.
2. `lucien stop` encerra o PTY e preserva a sessão localmente.
3. `lucien upload` sanitiza, cifra e enfileira um Job `PROCESSING`.
4. O worker extrai comandos e muda o Job para `PENDING`.
5. `lucien job <id_ou_nome_ou_indice>` permite selecionar comandos e redigir o
   procedimento; o índice corresponde à posição exibida por `lucien reviews`.
6. `lucien job sent <id_ou_nome_ou_indice>` envia o Markdown ao Hub.
7. O Hub sanitiza novamente e grava o runbook no provedor selecionado.
7. GitHub/Gitea compilam a wiki pelo modo escolhido; no disco local, o portal
   apresenta o arquivo sem escrever no volume.
8. `lucien runbook revise <uuid>` corrige um runbook já publicado, em qualquer
   provedor. Admins e seniors do domínio solicitam ao Hub uma nova revisão
   imutável; a versão anterior permanece para auditoria.

!!! warning "Documentação não é cofre"
    Tokens, senhas, chaves privadas e credenciais nunca devem aparecer nos
    runbooks. Use placeholders como `SUA_SENHA_AQUI`, `SEU_USER_REDIS_AQUI` e
    `SUA_KEY_EVOLUTION_AQUI`.

Consulte [Operação e segurança](operacao.md) antes de publicar um procedimento.
Para identidade e autorização, consulte [IAM, RBAC e metadados](iam-rbac.md).
