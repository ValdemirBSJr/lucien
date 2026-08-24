FROM python:3.13.14-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10004 lucien-wiki \
    && useradd --uid 10004 --gid 10004 --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin lucien-wiki \
    && install -d -o 10004 -g 10004 -m 0700 /workspace /publish /tmp/lucien-git-home

WORKDIR /app
COPY requirements.lock /app/requirements.lock
# --require-hashes: se os bytes de qualquer dependencia nao baterem com o
# lock, a construcao falha em vez de seguir com outra coisa. 
RUN python -m pip install --no-cache-dir --require-hashes \
      --requirement /app/requirements.lock

COPY app /app/app
COPY mkdocs.yml /app/mkdocs.yml
COPY bin/git-askpass.sh /app/bin/git-askpass.sh
RUN chmod 0555 /app/bin/git-askpass.sh \
    && chmod -R a-w /app

USER 10004:10004
ENTRYPOINT ["python", "-m", "app.main"]

FROM runtime-base AS test
USER root
COPY requirements-test.lock /app/requirements-test.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      --requirement /app/requirements-test.lock
COPY tests /app/tests
COPY pytest.ini /app/pytest.ini
# Só no estágio de teste: `tests/test_imagem.py` confere que o Dockerfile leva
# para dentro da imagem tudo que o serviço lê em execução. O runtime não o
# carrega.
COPY Dockerfile /app/Dockerfile
RUN chmod -R a-w /app
USER 10004:10004
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["-q", "-p", "no:cacheprovider"]

FROM runtime-base AS runtime
