"""Classificacao de criticidade: as evasoes que um pentest interno achou.

Duas metades que precisam passar juntas:
  A) os idiomas destrutivos que evadiam agora classificam ALTA;
  B) comandos legitimos do dia a dia continuam BAIXA/MEDIA -- senao a correcao
     apenas troca a evasao por um falso positivo que bloqueia junior a toa.
"""

import pytest

from app.domain.publication import classify_criticality, Criticality

DEVEM_SER_ALTA = [
    ("rm -rf /",                       "baseline ja coberto"),
    ("rm -r -f /data",                 "flags separadas (evasao 1)"),
    ("rm --recursive --force /data",   "forma longa"),
    ("rm -fr /data",                   "ordem invertida"),
    ("rm -f -r /var/lib",              "ordem invertida separada"),
    ("find / -delete",                 "find -delete (evasao 2)"),
    ("find /data -type f -delete",     "find -delete com filtro"),
    ("find /var -name '*.log' -exec rm {} +", "find -exec rm"),
    ("cat /dev/zero > /dev/sda",       "redirect para device (evasao 3)"),
    (": > /dev/nvme0n1",               "truncate device"),
    ("chmod -R 000 /etc",              "chmod recursivo em /etc"),
    ("chown -R nobody /usr",           "chown recursivo em /usr"),
    ("chmod -R 777 /tmp/x /",          "chmod recursivo terminando em /"),
    ("mv /etc /tmp/etc.bak",           "mover /etc"),
    ("> /etc/passwd",                  "truncar /etc/passwd"),
    (":(){ :|:& };:",                  "fork bomb"),
    ("dd if=/dev/zero of=/dev/sda",    "dd of=/dev/ (ja coberto)"),
    ("mkfs.ext4 /dev/sdb1",            "mkfs (ja coberto)"),
]

# O custo de errar aqui e alto: rotular alta demais BLOQUEIA junior de publicar
# procedimento rotineiro.
NAO_PODEM_SER_ALTA = [
    ("rm arquivo.log",                 "remover um arquivo"),
    ("rm -f /tmp/lock",                "force sem recursivo"),
    ("rm -r build/",                   "recursivo sem force, caminho relativo"),
    ("find /var/log -name '*.gz'",     "find sem delete"),
    ("find . -type f -name '*.py'",    "find comum"),
    ("echo ok > /dev/null",            "redirect para /dev/null"),
    ("cat /etc/passwd",                "LER /etc/passwd"),
    ("grep root /etc/passwd",          "grep em /etc/passwd"),
    ("chmod 640 /etc/lucien/env",      "chmod nao recursivo"),
    ("chown lucien:lucien /opt/app",   "chown nao recursivo"),
    ("chmod -R 750 /opt/minha-app",    "chmod recursivo fora de dir de sistema"),
    ("mv /opt/app /opt/app.bak",       "mover dir de aplicacao"),
    ("cp -r /etc/nginx /backup/",      "copiar /etc"),
    ("ls -la /etc",                    "listar /etc"),
    ("systemctl restart nginx",        "restart de servico (media)"),
    ("docker compose up -d",           "subir stack"),
    ("kubectl get pods",               "leitura no cluster"),
]



@pytest.mark.parametrize("comando,descricao", DEVEM_SER_ALTA)
def test_idioma_destrutivo_classifica_alta(comando: str, descricao: str) -> None:
    """Cada um destes ja passou como criticidade baixa em algum momento.

    O denylist original casava `rm` so com o cluster unico de flags (`-rf`),
    entao `rm -r -f /` escapava -- e um junior publicava. Idiomas sem `rm`
    (`find -delete`, redirect para device, chmod recursivo na raiz) nem
    estavam na lista.
    """

    assert classify_criticality([comando]) is Criticality.HIGH, descricao


@pytest.mark.parametrize("comando,descricao", NAO_PODEM_SER_ALTA)
def test_comando_rotineiro_nao_classifica_alta(comando: str, descricao: str) -> None:
    """A outra metade da correcao, e a que se perde de vista.

    Rotular alta demais nao e conservador: BLOQUEIA um junior de publicar
    procedimento rotineiro. Ampliar o denylist sem esta metade so troca a
    evasao por um falso positivo.
    """

    assert classify_criticality([comando]) is not Criticality.HIGH, descricao
