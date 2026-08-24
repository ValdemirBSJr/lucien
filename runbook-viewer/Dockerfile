FROM python:3.13.14-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/viewer
COPY runbook-viewer/pyproject.toml runbook-viewer/requirements.lock ./
COPY runbook-viewer/app ./app
COPY logo-lucien.png ./app/static/logo.png
# --require-hashes: se os bytes de qualquer dependencia nao baterem com o
# lock, a construcao falha em vez de seguir com outra coisa. O proprio
# pacote entra depois, sem resolver dependencia e sem isolamento de build --
# o setuptools ja veio do lock, verificado.
RUN pip install --no-cache-dir --require-hashes --requirement requirements.lock \
    && pip install --no-cache-dir --no-deps --no-build-isolation . \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin viewer

USER 10001:10001
EXPOSE 9091

FROM base AS test
USER root
COPY runbook-viewer/requirements-test.lock ./
RUN pip install --no-cache-dir --require-hashes --requirement requirements-test.lock
COPY runbook-viewer/tests ./tests
# Golden files produzidos pelo Hub; ver backend/contracts/README.md.
COPY backend/contracts ./backend/contracts
USER 10001:10001
CMD ["pytest", "-q"]

FROM base AS runtime
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "9091", "--ssl-keyfile", "/certs/server.key", "--ssl-certfile", "/certs/server.crt", "--no-proxy-headers"]
