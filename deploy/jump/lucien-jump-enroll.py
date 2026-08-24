#!/usr/bin/env python3
"""Provisiona o usuário SSH no Hub sem expor credenciais em argv ou logs."""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import secrets
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_FILE = Path("/etc/lucien/jump.conf")
TOKEN_FILE = Path("/etc/lucien/secrets/jump_enrollment_key")
ALLOWED_DOMAINS = {
    "1": "acessos",
    "2": "servidores",
    "3": "redes",
    "4": "suporte",
}


class EnrollmentError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def read_config() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or key not in {
            "API_HOST",
            "TLS_CA_FILE",
            "LUCIEN_BINARY",
        }:
            raise RuntimeError("configuração inválida do jump server")
        values[key] = value
    required = {"API_HOST", "TLS_CA_FILE", "LUCIEN_BINARY"}
    if set(values) != required or not values["API_HOST"].startswith("https://"):
        raise RuntimeError("configuração incompleta do jump server")
    return values


def current_ldap_user() -> tuple[str, pwd.struct_passwd]:
    if os.geteuid() != 0:
        raise RuntimeError("o helper deve ser executado pelo sudoers restrito")
    username = os.environ.get("SUDO_USER", "")
    if re.fullmatch(r"[A-Za-z][0-9]+", username) is None:
        raise RuntimeError("identidade POSIX inválida para provisionamento")
    account = pwd.getpwnam(username)
    primary_gid = grp.getgrnam("lucien-primary").gr_gid
    if primary_gid not in os.getgrouplist(username, account.pw_gid):
        raise RuntimeError("usuário não pertence ao grupo LDAP autorizado")
    return username, account


def full_name_from_gecos(account: pwd.struct_passwd) -> str | None:
    """Primeiro campo do GECOS, que o SSSD preenche com o nome do LDAP.

    O GECOS e `Nome Completo,sala,telefone,telefone,outros`. Enviar o campo
    inteiro colocaria telefone e sala no runbook publicado, entao o recorte
    acontece aqui, onde o formato e conhecido. O Hub sanea de novo, mas nao
    tem como saber que o quarto campo era um telefone.
    """

    bruto = (account.pw_gecos or "").split(",")[0]
    nome = " ".join(bruto.split())
    if not nome or nome == account.pw_name:
        # Sem nome no LDAP o GECOS costuma repetir o login; nesse caso o Hub
        # cai para o username sozinho, e mandar o valor so gera ruido.
        return None
    return nome[:120]


def request_enrollment(
    config: dict[str, str],
    token: str,
    username: str,
    domain: str | None,
    full_name: str | None = None,
) -> dict[str, object]:
    body: dict[str, str] = {"username": username}
    if domain is not None:
        body["domain_function"] = domain
    if full_name is not None:
        body["display_name"] = full_name
    encoded = json.dumps(body, separators=(",", ":")).encode()
    idempotency_key = secrets.token_hex(16)
    context = ssl.create_default_context(cafile=config["TLS_CA_FILE"])
    request = urllib.request.Request(
        f"{config['API_HOST'].rstrip('/')}/auth/jump/enroll",
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, context=context, timeout=15) as response:
                payload = response.read(65_537)
                if len(payload) > 65_536:
                    raise RuntimeError("resposta do Hub excede o limite")
                result = json.loads(payload)
                if not isinstance(result, dict):
                    raise RuntimeError("resposta inválida do Hub")
                return result
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read(8192)).get("detail", "erro do Hub")
            except (AttributeError, json.JSONDecodeError):
                detail = "erro do Hub"
            raise EnrollmentError(error.code, str(detail)) from error
        except (TimeoutError, urllib.error.URLError):
            if attempt == 1:
                raise RuntimeError("Hub indisponível durante o provisionamento")
    raise RuntimeError("falha inesperada no provisionamento")


def select_domain() -> str:
    # Leitura e escrita separadas de proposito. `r+` monta um
    # BufferedRandom, que exige stream seekable -- e /dev/tty e dispositivo
    # de caractere. Onde isso nao e tolerado, o primeiro login morre com
    # "File or stream is not seekable" e o operador fica sem Lucien.
    with open("/dev/tty", "w", encoding="utf-8") as saida:
        saida.write(
            "Selecione sua área no Lucien:\n"
            "  1 - Acessos\n"
            "  2 - Servidores\n"
            "  3 - Network\n"
            "  4 - Suporte\n"
            "Opção: "
        )
        saida.flush()
    with open("/dev/tty", "r", encoding="utf-8") as entrada:
        selected = entrada.readline().strip()
    domain = ALLOWED_DOMAINS.get(selected)
    if domain is None:
        raise RuntimeError("opção de área inválida")
    return domain


def save_user_token(
    config: dict[str, str], username: str, account: pwd.struct_passwd, token: str
) -> None:
    command = [
        "/usr/sbin/runuser",
        "-u",
        username,
        "--",
        "/usr/bin/env",
        f"HOME={account.pw_dir}",
        f"API_HOST={config['API_HOST']}",
        f"TLS_CA_FILE={config['TLS_CA_FILE']}",
        "LUCIEN_ALLOW_FILE_TOKEN=true",
        "LUCIEN_JUMP_MODE=true",
        f"LUCIEN_EXPECTED_USERNAME={username}",
        config["LUCIEN_BINARY"],
        "login",
        "--token-stdin",
        "--quiet",
    ]
    result = subprocess.run(
        command,
        input=f"{token}\n".encode(),
        stdout=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("o CLI não conseguiu armazenar a credencial do usuário")


def main() -> int:
    try:
        username, account = current_ldap_user()
        config = read_config()
        service_token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if not service_token.startswith("luc_jump_") or len(service_token) > 4096:
            raise RuntimeError("credencial técnica inválida")
        try:
            full_name = full_name_from_gecos(account)
            result = request_enrollment(
                config, service_token, username, None, full_name
            )
        except EnrollmentError as error:
            if error.status != 422 or "domain_function" not in error.detail:
                raise
            domain = select_domain()
            result = request_enrollment(
                config, service_token, username, domain, full_name
            )
        provisional = result.get("provisional_token")
        role_level = result.get("role_level")
        if (
            result.get("username") != username
            or role_level not in {"junior", "pleno", "senior"}
            or not isinstance(provisional, str)
            or not provisional.startswith("luc_tmp_")
        ):
            raise RuntimeError("Hub retornou identidade incompatível")
        save_user_token(config, username, account, provisional)
        return 0
    except (EnrollmentError, OSError, RuntimeError, ValueError) as error:
        print(f"Lucien indisponível: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
