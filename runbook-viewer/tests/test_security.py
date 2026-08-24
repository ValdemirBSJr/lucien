import base64
import hashlib

import httpx
import pytest
from cryptography.fernet import Fernet

from app.security import (
    EditFormCipher,
    EditFormState,
    HubClient,
    HubIdentityVerifier,
    IdentityUnavailableError,
    InvalidCredentialsError,
    RevisionPreconditionFailedError,
    SessionCipher,
    SessionCredential,
)


@pytest.mark.asyncio
async def test_hub_verifier_confirma_username_e_usuario_ativo() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer luc_token_valido"
        return httpx.Response(
            200,
            json={
                "id": "user-1",
                "username": "operador",
                "role_level": "senior",
                "domain_function": "servidores",
                "is_active": True,
            },
        )

    async with httpx.AsyncClient(
        base_url="https://hub:8443", transport=httpx.MockTransport(handler)
    ) as client:
        user = await HubIdentityVerifier(client).verify(
            "operador", "luc_token_valido"
        )
    assert user.username == "operador"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_hub_verifier_falha_fechada_para_credencial_invalida(status: int) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status))
    async with httpx.AsyncClient(
        base_url="https://hub:8443", transport=transport
    ) as client:
        with pytest.raises(InvalidCredentialsError):
            await HubIdentityVerifier(client).verify("operador", "luc_token_invalido")


@pytest.mark.asyncio
async def test_hub_verifier_distingue_indisponibilidade_sem_vazar_credencial() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(502))
    async with httpx.AsyncClient(
        base_url="https://hub:8443", transport=transport
    ) as client:
        with pytest.raises(IdentityUnavailableError):
            await HubIdentityVerifier(client).verify("operador", "luc_token_secreto")


@pytest.mark.asyncio
async def test_hub_client_envia_revisao_com_headers_de_concorrencia() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["Authorization"]
        captured["idempotency"] = request.headers["Idempotency-Key"]
        captured["if_match"] = request.headers["If-Match"]
        captured["body"] = request.content
        return httpx.Response(201, json={"status": "PUBLISHED"})

    async with httpx.AsyncClient(
        base_url="https://hub:8443", transport=httpx.MockTransport(handler)
    ) as client:
        await HubClient(client).create_revision(
            "11111111-1111-4111-8111-111111111111",
            "# Revisado\n",
            "a" * 64,
            "revision-22222222-2222-4222-8222-222222222222",
            "luc_token_secreto",
        )

    assert captured["path"].endswith(
        "/runbooks/11111111-1111-4111-8111-111111111111/revisions"
    )
    assert captured["authorization"] == "Bearer luc_token_secreto"
    assert captured["idempotency"] == "revision-22222222-2222-4222-8222-222222222222"
    assert captured["if_match"] == '"' + ("a" * 64) + '"'
    assert captured["body"] == b'{"markdown":"# Revisado\\n"}'


@pytest.mark.asyncio
async def test_hub_client_preserva_semantica_da_precondicao_412() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(412))
    async with httpx.AsyncClient(
        base_url="https://hub:8443", transport=transport
    ) as client:
        with pytest.raises(RevisionPreconditionFailedError):
            await HubClient(client).create_revision(
                "11111111-1111-4111-8111-111111111111",
                "# Revisado\n",
                "a" * 64,
                "revision-22222222-2222-4222-8222-222222222222",
                "luc_token_secreto",
            )


@pytest.mark.asyncio
async def test_hub_client_obtem_catalogo_publicado_autenticado() -> None:
    identifiers = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/runbooks/published"
        assert request.headers["Authorization"] == "Bearer luc_token_secreto"
        return httpx.Response(200, json={"ids": identifiers})

    async with httpx.AsyncClient(
        base_url="https://hub:8443", transport=httpx.MockTransport(handler)
    ) as client:
        published = await HubClient(client, max_catalog_ids=2).list_published_ids(
            "luc_token_secreto"
        )

    assert published == frozenset(identifiers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,max_ids",
    [
        ({"ids": ["não-e-uuid"]}, 10),
        (
            {
                "ids": [
                    "11111111-1111-4111-8111-111111111111",
                    "11111111-1111-4111-8111-111111111111",
                ]
            },
            10,
        ),
        (
            {
                "ids": [
                    "11111111-1111-4111-8111-111111111111",
                    "22222222-2222-4222-8222-222222222222",
                ]
            },
            1,
        ),
    ],
)
async def test_hub_client_rejeita_catalogo_malformado(
    payload: dict[str, list[str]], max_ids: int
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(
        base_url="https://hub:8443", transport=transport
    ) as client:
        with pytest.raises(IdentityUnavailableError):
            await HubClient(client, max_catalog_ids=max_ids).list_published_ids(
                "luc_token_secreto"
            )


def test_cookie_cifra_e_autentica_credencial() -> None:
    secret = "s" * 48
    cipher = SessionCipher(secret, 900)
    credential = SessionCredential("operador", "luc_token_muito_secreto")

    sealed = cipher.seal(credential)

    assert "luc_token_muito_secreto" not in sealed
    assert cipher.open(sealed) == credential
    with pytest.raises(InvalidCredentialsError):
        cipher.open(sealed[:-1] + ("A" if sealed[-1] != "A" else "B"))

    # Confirma que uma chave diferente não consegue abrir a sessão.
    other_key = base64.urlsafe_b64encode(hashlib.sha256(b"outro-segredo").digest())
    with pytest.raises(Exception):
        Fernet(other_key).decrypt(sealed.encode("ascii"))


def test_estado_de_edicao_e_cifrado_e_nao_pode_ser_adulterado() -> None:
    cipher = EditFormCipher("s" * 48)
    state = EditFormState(
        root_id="11111111-1111-4111-8111-111111111111",
        current_job_id="22222222-2222-4222-8222-222222222222",
        body_hash="a" * 64,
        csrf_token="c" * 43,
        idempotency_key="revision-33333333-3333-4333-8333-333333333333",
    )
    sealed = cipher.seal(state)

    assert state.body_hash not in sealed
    assert cipher.open(sealed) == state
    with pytest.raises(InvalidCredentialsError):
        cipher.open(sealed[:-2] + "AA")
