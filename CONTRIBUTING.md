# Como contribuir / Contributing

Obrigado por contribuir com o Lucien. Mudanças devem preservar o Hub como
autoridade de identidade, RBAC, sanitização e publicação.

Thank you for contributing to Lucien. Changes must preserve the Hub as the
authority for identity, RBAC, sanitization, and publication.

## Fluxo / Workflow

1. Crie um fork e uma branch pequena por assunto.
2. Adicione ou ajuste o teste que demonstra a mudança.
3. Execute `bash scripts/verify.sh` em Linux ou Git Bash com Docker.
4. Abra um Pull Request descrevendo comportamento, risco e validação.

1. Fork the repository and create a small, focused branch.
2. Add or update the test that demonstrates the change.
3. Run `bash scripts/verify.sh` on Linux or Git Bash with Docker.
4. Open a Pull Request describing behavior, risk, and verification.

## Revisão e merge / Review and merge

A `main` é protegida: nada entra sem Pull Request. Não há aprovação
obrigatória -- o que libera o merge são os status checks do CI, todos
obrigatórios. Enquanto um deles estiver vermelho, o botão de merge aparece
cinza com "Merging is blocked"; não é falta de permissão, é check reprovado.
O botão fica no fim da aba Conversation, abaixo da caixa de checks.

Comentário de review em aberto também trava o merge: a resolução de threads é
exigida. Resolva o que você mesmo abriu antes de mergear.

Os três métodos estão permitidos. Prefira rebase, que mantém os commits
separados como no restante do histórico.

Para acompanhar sem sair do terminal:

```sh
gh pr checks <numero> --watch
gh run view --job <id> --log-failed
```

O portão `segredos` merece atenção especial: ele varre o **histórico inteiro**,
não só o diff do PR. Corrigir um achado num commit novo não limpa o commit
antigo -- o portão continua vermelho. Reescreva a branch e faça force-push antes
do merge.

Quando o achado for um exemplo que precisa mostrar exatamente o texto que
dispara a regra -- documentação das regras, fixture de teste do scanner --,
acrescente `gitleaks:allow` como comentário na mesma linha. Isso vale para o
código-fonte deste repositório, e só para ele: nunca instrua um operador a usar
esse comentário em conteúdo de runbook, porque ali ele desliga a política de
segredos do Hub.

---

`main` is protected: nothing lands without a Pull Request. No approving review
is required -- what unblocks the merge are the CI status checks, all of them
required. While any one is red, the merge button shows greyed out with
"Merging is blocked"; that is a failing check, not a missing permission. The
button sits at the bottom of the Conversation tab, below the checks box.

An open review comment also blocks the merge: thread resolution is required.
Resolve the ones you opened yourself before merging.

All three merge methods are allowed. Prefer rebase, which keeps commits
separate as the rest of the history does.

To follow along without leaving the terminal:

```sh
gh pr checks <number> --watch
gh run view --job <id> --log-failed
```

The `segredos` gate deserves special care: it scans the **entire history**, not
just the PR diff. Fixing a finding in a new commit does not clear the old
commit -- the gate stays red. Rewrite the branch and force-push before merging.

When the finding is an example that must show the exact text that trips the
rule -- rule documentation, a scanner test fixture --, append `gitleaks:allow`
as a comment on the same line. This applies to this repository's source, and
only to it: never instruct an operator to use that comment in runbook content,
where it switches off the Hub's secret policy.

## Dados permitidos / Allowed data

Use somente identidades fictícias, IPv4 privados ou faixas reservadas para
documentação e hostnames terminados em `.example`. Nunca envie sessão real,
token, chave, certificado privado, matrícula, nome de equipamento ou endereço
de infraestrutura.

Use only fictional identities, private IPv4 addresses or documentation ranges,
and hostnames ending in `.example`. Never submit a real session, token, key,
private certificate, employee identifier, device name, or infrastructure
address.

Falhas de segurança seguem [SECURITY.md](SECURITY.md), não issues públicas.
Security vulnerabilities follow [SECURITY.md](SECURITY.md), not public issues.

## Estilo e escopo / Style and scope

- mudanças pequenas, rastreáveis e sem reformatação ampla;
- nenhuma credencial ou endereço hardcoded;
- dependências e Actions fixadas por versão imutável ou digest;
- documentação pública em português brasileiro e, quando possível, equivalente
  em inglês;
- compatibilidade de leitura com artefatos já publicados.

Ao alterar `cli/go.mod` ou `cli/go.sum`, execute
`bash scripts/update-cli-notices.sh` e inclua a atualização de
`THIRD-PARTY-NOTICES.txt` no mesmo Pull Request. O empacotador não gera pacotes
sem os avisos de licença.

When changing `cli/go.mod` or `cli/go.sum`, run
`bash scripts/update-cli-notices.sh` and include the updated
`THIRD-PARTY-NOTICES.txt` in the same Pull Request. The packaging script refuses
to build archives without the license notices.

By submitting a contribution, you agree that it is licensed under the
[Apache License 2.0](LICENSE).
