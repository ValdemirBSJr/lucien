# Política de segurança / Security policy

## Versões suportadas / Supported versions

Enquanto o projeto estiver antes da primeira versão estável pública, correções
de segurança serão preparadas somente para a versão mais recente da branch
`main`. Depois do primeiro release, esta tabela será atualizada com as versões
mantidas.

Before the first stable public release, security fixes are prepared only for
the latest version on `main`. This table will be updated with supported release
lines after the first release.

## Divulgação responsável / Responsible disclosure

Não abra issue pública para vulnerabilidade, credencial exposta ou dado de
infraestrutura. Use **Security → Advisories → Report a vulnerability** no
[repositório do Lucien](https://github.com/ValdemirBSJr/lucien/security/advisories/new).

Do not open a public issue for a vulnerability, exposed credential, or
infrastructure data. Use **Security → Advisories → Report a vulnerability** in
the [Lucien repository](https://github.com/ValdemirBSJr/lucien/security/advisories/new).

Inclua apenas o mínimo necessário para reproduzir o problema. Redija tokens,
logs, usernames, nomes, IPs, hostnames, certificados e conteúdo de runbooks. Se
um artefato sensível for indispensável, aguarde instruções no advisory privado.

Include only the minimum information required to reproduce the issue. Redact
tokens, logs, usernames, names, IP addresses, hostnames, certificates, and
runbook content. If a sensitive artifact is essential, wait for instructions
in the private advisory.

## Escopo prioritário / Priority scope

- autenticação, tokens e bootstrap de administradores;
- RBAC e isolamento entre usuários e áreas;
- sanitização, DLP e detecção de segredos;
- publicação imutável e idempotência;
- fronteiras entre Hub, CLI, SLM, scanner e providers Git;
- execução indevida por workflows, hooks ou conteúdo de runbooks.

Reports outside this list are welcome when they affect confidentiality,
integrity, availability, or the documented security boundaries.
