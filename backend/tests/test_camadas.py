"""A regra de dependência entre camadas, verificada no próprio código.

Domínio e aplicação não podem depender de infraestrutura. A regra é fácil de
enunciar e fácil de quebrar sem querer: um import conveniente resolve o
problema da hora e nada reclama depois -- foi assim que `application.py` passou
a importar o hash de credencial, a redação de segredo e a trilha de auditoria.

O critério para decidir onde algo mora não é o assunto, é a natureza. Política
pura -- que regra vale, o que pode sair, o que fica registrado -- é domínio,
mesmo quando envolve HMAC ou logging. Mecanismo substituível -- banco, HTTP,
provedor Git, para onde o log escoa -- é infraestrutura.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# Raízes de composição: existem justamente para conhecer a infraestrutura e
# ligá-la ao resto. `api/` é o adaptador de entrega e cabe no mesmo grupo.
ADAPTADORES = {
    "main.py",
    "worker.py",
    "recover_admin.py",
    "issue_jump_enrollment_key.py",
    "export_wiki.py",
}


def _modulos_importados(arquivo: Path) -> set[str]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
    modulos: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module:
            modulos.add(no.module)
        elif isinstance(no, ast.Import):
            modulos.update(alias.name for alias in no.names)
    return modulos


def _arquivos_da_camada(nome: str) -> list[Path]:
    if nome == "application":
        return [APP / "application.py"]
    return sorted((APP / nome).glob("*.py"))


def test_dominio_e_aplicacao_ignoram_a_infraestrutura() -> None:
    violacoes = []
    for camada in ("domain", "application"):
        for arquivo in _arquivos_da_camada(camada):
            for modulo in _modulos_importados(arquivo):
                if modulo.startswith("app.infrastructure"):
                    violacoes.append(f"{arquivo.name} importa {modulo}")
    assert not violacoes, (
        "camada interna dependendo de infraestrutura: "
        + "; ".join(sorted(violacoes))
    )


def test_infraestrutura_nao_conhece_a_aplicacao() -> None:
    """A seta aponta para dentro. Infra implementa portas, não chama serviços."""
    violacoes = [
        f"{arquivo.name} importa {modulo}"
        for arquivo in _arquivos_da_camada("infrastructure")
        for modulo in _modulos_importados(arquivo)
        if modulo == "app.application" or modulo.startswith("app.application.")
    ]
    assert not violacoes, "; ".join(sorted(violacoes))


def test_dominio_nao_depende_de_framework_web() -> None:
    """FastAPI e Starlette pertencem à borda, não à regra de negócio."""
    proibidos = ("fastapi", "starlette", "sqlalchemy", "httpx")
    violacoes = [
        f"{arquivo.name} importa {modulo}"
        for arquivo in _arquivos_da_camada("domain")
        for modulo in _modulos_importados(arquivo)
        if modulo.split(".")[0] in proibidos
    ]
    assert not violacoes, "; ".join(sorted(violacoes))


def test_adaptadores_sao_os_unicos_a_montar_a_infraestrutura() -> None:
    """Guarda a lista: um arquivo novo que monte infraestrutura precisa entrar
    nela de propósito, e não por descuido."""
    montam = {
        arquivo.name
        for arquivo in APP.glob("*.py")
        if any(
            modulo.startswith("app.infrastructure")
            for modulo in _modulos_importados(arquivo)
        )
    }
    assert montam == ADAPTADORES, f"esperado {sorted(ADAPTADORES)}, obtido {sorted(montam)}"
