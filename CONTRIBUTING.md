# Como contribuir / Contributing

Obrigado por contribuir com o Lucien. Mudanças devem preservar o Hub como
autoridade de identidade, RBAC, sanitização e publicação.

Thank you for contributing to Lucien. Changes must preserve the Hub as the
authority for identity, RBAC, sanitization, and publication.

## Fluxo / Workflow

1. Crie um fork e uma branch pequena por assunto.
2. Adicione ou ajuste o teste que demonstra a mudança.
3. Execute `bash scripts/verificar.sh` em Linux ou Git Bash com Docker.
4. Abra um Pull Request descrevendo comportamento, risco e validação.

1. Fork the repository and create a small, focused branch.
2. Add or update the test that demonstrates the change.
3. Run `bash scripts/verificar.sh` on Linux or Git Bash with Docker.
4. Open a Pull Request describing behavior, risk, and verification.

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
`bash scripts/atualizar-avisos-cli.sh` e inclua a atualização de
`THIRD-PARTY-NOTICES.txt` no mesmo Pull Request. O empacotador não gera pacotes
sem os avisos de licença.

When changing `cli/go.mod` or `cli/go.sum`, run
`bash scripts/atualizar-avisos-cli.sh` and include the updated
`THIRD-PARTY-NOTICES.txt` in the same Pull Request. The packaging script refuses
to build archives without the license notices.

By submitting a contribution, you agree that it is licensed under the
[Apache License 2.0](LICENSE).
