# Contratos entre o Hub e seus consumidores

Estes arquivos são a forma exata dos dados que o Hub produz. Eles não são
exemplos: são **golden files** verificados dos dois lados.

- `backend/tests/test_contracts.py` regenera cada arquivo a partir do código
  real e compara. Se o Hub mudar a forma de uma resposta ou do frontmatter, o
  teste falha e obriga a atualizar o arquivo conscientemente.
- `runbook-viewer/tests/test_contracts.py` lê os mesmos arquivos e os valida
  com os schemas do portal. Se o portal não conseguir ler o que o Hub produz,
  o teste falha.

A separação existe porque os dois serviços têm um pacote chamado `app` e não
podem ser importados no mesmo processo. O arquivo no meio é o que os obriga a
concordar.

Ao atualizar um contrato: rode o teste do backend, aceite o novo arquivo, e
só então corrija o portal. Se o teste do portal quebrar, o contrato mudou de
forma incompatível -- essa é a informação que ele existe para dar.
