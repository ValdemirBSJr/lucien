# Testes de publicação

- `wiki-local`: laboratório isolado e executável do `wiki-builder`.

O ensaio usa somente dados fictícios. Tokens, certificados reais e runbooks de
produção não pertencem a esta árvore. A configuração efêmera do Docker fica em
`${XDG_STATE_HOME:-$HOME/.local/state}/lucien-publisher`; as chaves existem
somente no volume Docker `demo-certs` e são recriadas a cada execução.
