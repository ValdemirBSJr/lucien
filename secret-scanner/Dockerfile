FROM ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f AS gitleaks

FROM python:3.13.14-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
COPY pyproject.toml requirements.lock ./
COPY gitleaks-lucien.toml /etc/lucien/gitleaks.toml
COPY app ./app
# --require-hashes: se os bytes de qualquer dependencia nao baterem com o
# lock, a construcao falha em vez de seguir com outra coisa. O proprio
# pacote entra depois, sem resolver dependencia e sem isolamento de build --
# o setuptools ja veio do lock, verificado.
RUN pip install --no-cache-dir --require-hashes --requirement requirements.lock \
    && pip install --no-cache-dir --no-deps --no-build-isolation . \
    && useradd --create-home --uid 10002 --shell /usr/sbin/nologin scanner


FROM base AS test
USER root
COPY requirements-test.lock ./
RUN pip install --no-cache-dir --require-hashes --requirement requirements-test.lock
COPY tests ./tests
COPY pytest.ini ./pytest.ini
USER 10002:10002
CMD ["pytest", "-q"]

FROM base AS runtime
USER 10002:10002
EXPOSE 8090
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--no-proxy-headers"]
