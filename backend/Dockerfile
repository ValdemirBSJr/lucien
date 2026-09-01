FROM python:3.13.14-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app
# pytesseract e um wrapper fino: precisa do binario tesseract no PATH em
# tempo de execucao, nao so da lib Python instalada pelo pip abaixo.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        tesseract-ocr tesseract-ocr-por tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.lock ./
COPY app ./app
# O runner de migracoes le os arquivos .sql em tempo de execucao.
COPY migrations ./migrations
# --require-hashes: se os bytes de qualquer dependencia nao baterem com o
# lock, a construcao falha em vez de seguir com outra coisa. O proprio
# pacote entra depois, sem resolver dependencia e sem isolamento de build --
# o setuptools ja veio do lock, verificado.
RUN pip install --no-cache-dir --require-hashes --requirement requirements.lock \
    && pip install --no-cache-dir --no-deps --no-build-isolation . \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin hub \
    && install -d -o hub -g hub -m 0750 /data/playbooks

USER 10001:10001
EXPOSE 8443

FROM base AS test
USER root
COPY requirements-test.lock ./
RUN pip install --no-cache-dir --require-hashes --requirement requirements-test.lock
COPY tests ./tests
# Golden files verificados dos dois lados; ver contracts/README.md.
COPY contracts ./contracts
USER 10001:10001
CMD ["pytest", "-q"]

FROM base AS runtime
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8443", "--ssl-keyfile", "/certs/server.key", "--ssl-certfile", "/certs/server.crt", "--no-proxy-headers"]
