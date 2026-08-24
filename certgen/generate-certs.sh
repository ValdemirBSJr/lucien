#!/bin/sh
set -eu

# Gera uma CA privada e um certificado de servidor com SANs explícitos.
# Os artefatos são gravados no volume /certs e nunca devem entrar no Git.
umask 077
mkdir -p /certs

if [ -e /certs/ca.key ] || [ -e /certs/server.key ]; then
  echo "Recusado: certificados já existem em /certs. Remova-os conscientemente para rotacionar." >&2
  exit 1
fi

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out /certs/ca.key
openssl req -x509 -new -sha256 -days 3650 \
  -key /certs/ca.key \
  -subj "/C=BR/O=Lucien/CN=Lucien Internal CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" \
  -out /certs/ca.crt

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out /certs/server.key
openssl req -new -sha256 \
  -key /certs/server.key \
  -subj "/C=BR/O=Lucien/CN=runbook-hub" \
  -out /certs/server.csr

{
  echo "basicConstraints=critical,CA:FALSE"
  echo "keyUsage=critical,digitalSignature,keyEncipherment"
  echo "extendedKeyUsage=serverAuth"
  printf "subjectAltName="
  first=1
  old_ifs=$IFS
  IFS=','
  for dns in ${CERT_DNS:-hub,localhost}; do
    [ "$first" -eq 1 ] || printf ","
    printf "DNS:%s" "$dns"
    first=0
  done
  for ip in ${CERT_IP:-127.0.0.1}; do
    [ "$first" -eq 1 ] || printf ","
    printf "IP:%s" "$ip"
    first=0
  done
  IFS=$old_ifs
  printf "\n"
} > /certs/server.ext

openssl x509 -req -sha256 -days 397 \
  -in /certs/server.csr \
  -CA /certs/ca.crt \
  -CAkey /certs/ca.key \
  -CAcreateserial \
  -extfile /certs/server.ext \
  -out /certs/server.crt

openssl verify -CAfile /certs/ca.crt /certs/server.crt
rm -f /certs/server.csr /certs/server.ext /certs/ca.srl
chmod 0600 /certs/ca.key /certs/server.key
chmod 0644 /certs/ca.crt /certs/server.crt
# O Hub executa com UID 10001 e precisa ler somente sua própria chave.
chown 10001:10001 /certs/server.key /certs/server.crt

echo "Certificados gerados em /certs. Proteja ca.key fora do host de aplicação."
