FROM alpine:3.23.5@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40

RUN apk add --no-cache openssl
COPY generate-certs.sh /usr/local/bin/generate-certs
RUN chmod 0755 /usr/local/bin/generate-certs

ENTRYPOINT ["generate-certs"]
