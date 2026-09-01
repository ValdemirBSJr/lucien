#!/usr/bin/env bash
# Empacota o app desktop `lucien-desktop` para distribuição.
#
# Diferente de scripts/build-cli.sh, que constrói dentro de um contêiner e
# cross-compila para quatro alvos, este roda na máquina do operador: o Wails
# precisa do webview nativo do sistema (WebView2 no Windows, WebKitGTK no
# Linux, WKWebView no macOS) e não cross-compila. Cada plataforma é empacotada
# onde for construída.
#
# Uso:
#   VERSION=0.1.0 bash scripts/build-desktop.sh
set -euo pipefail

VERSION="${VERSION:-dev}"

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR="$ROOT_DIR/cli/desktop"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/dist}"
FONT_LICENSE="$DESKTOP_DIR/frontend/src/assets/fonts/geist/LICENSE.txt"

erro() { printf 'Error: %s\n' "$1" >&2; exit 1; }

# O mesmo formato que o build do CLI aceita: o valor vira nome de arquivo.
[[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || erro 'VERSION contains invalid characters'

command -v wails >/dev/null 2>&1 || \
  erro 'wails is missing; install it with: go install github.com/wailsapp/wails/v2/cmd/wails@latest'

[[ -f "$ROOT_DIR/LICENSE" ]] || erro 'LICENSE is missing'
[[ -f "$ROOT_DIR/NOTICE" ]] || erro 'NOTICE is missing'
[[ -f "$ROOT_DIR/THIRD-PARTY-NOTICES.txt" ]] || \
  erro 'THIRD-PARTY-NOTICES.txt is missing; run scripts/update-cli-notices.sh'
# A fonte Geist é embutida no binário pelo frontend, então a licença dela viaja
# junto -- a SIL OFL exige que o texto acompanhe o software que a distribui.
[[ -f "$FONT_LICENSE" ]] || erro 'the Geist font license is missing'

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) plataforma='windows_amd64'; binario='lucien-desktop.exe' ;;
  Linux)                plataforma='linux_amd64';   binario='lucien-desktop' ;;
  Darwin)               plataforma='darwin_universal'; binario='lucien-desktop.app' ;;
  *) erro "unsupported build host: $(uname -s)" ;;
esac

mkdir -p "$OUTPUT_DIR"

# -X main.version é o mesmo mecanismo do CLI: uma versão só, decidida aqui, sem
# um número escrito à mão dentro do código.
(cd "$DESKTOP_DIR" && wails build -clean -ldflags "-X main.version=$VERSION")

construido="$DESKTOP_DIR/build/bin/$binario"
[[ -e "$construido" ]] || erro "wails did not produce $construido"

package_name="lucien-desktop_${VERSION}_${plataforma}"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT
package_dir="$TEMP_DIR/$package_name"
mkdir -p "$package_dir"

cp -R "$construido" "$package_dir/$binario"
install -m 0644 "$ROOT_DIR/LICENSE" "$package_dir/LICENSE"
install -m 0644 "$ROOT_DIR/NOTICE" "$package_dir/NOTICE"
install -m 0644 "$ROOT_DIR/THIRD-PARTY-NOTICES.txt" "$package_dir/THIRD-PARTY-NOTICES.txt"
install -m 0644 "$FONT_LICENSE" "$package_dir/LICENSE-Geist.txt"

cat > "$package_dir/LEIA-ME.txt" <<EOF
Lucien Desktop $VERSION ($plataforma)

Autoria manual de runbooks. O app fala com o Hub por HTTPS e nao substitui o
CLI: a captura de sessao de terminal continua sendo do 'lucien'.

Este pacote contem somente o aplicativo. Nao contem token, certificado nem
configuracao do Hub. Verifique o SHA-256 antes de instalar.

Na primeira execucao, informe o endereco do Hub e o arquivo da CA publica na
tela de configuracao. Distribua somente a CA publica.

O binario nao e assinado. O Windows pode exibir aviso do SmartScreen.

As licencas do Lucien, das dependencias compiladas e da fonte Geist
(SIL Open Font License 1.1) acompanham este pacote.
EOF

archive="$OUTPUT_DIR/${package_name}.zip"
rm -f -- "$archive"
# -r porque o pacote do macOS e um diretorio .app, nao um arquivo unico.
#
# O Git Bash do Windows nao traz `zip`, e este script precisa rodar justamente
# la para o pacote Windows. O Python ja e exigido pelo resto do projeto, e o
# zipfile preserva o bit de execucao pelo external_attr -- sem isso o binario
# sairia sem permissao em Linux e macOS.
if command -v zip >/dev/null 2>&1; then
  (cd "$TEMP_DIR" && zip -q -r "$archive" "$package_name")
else
  command -v python >/dev/null 2>&1 || erro 'neither zip nor python is available to build the archive'
  ARCHIVE="$archive" PACKAGE="$package_name" python -c '
import os
import sys
import zipfile

raiz = sys.argv[1]
destino = os.environ["ARCHIVE"]
pacote = os.environ["PACKAGE"]

with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
    for atual, _, arquivos in os.walk(os.path.join(raiz, pacote)):
        for nome in sorted(arquivos):
            completo = os.path.join(atual, nome)
            interno = os.path.relpath(completo, raiz).replace(os.sep, "/")
            info = zipfile.ZipInfo.from_file(completo, interno)
            info.external_attr = (os.stat(completo).st_mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(completo, "rb") as f, z.open(info, "w") as saida:
                saida.write(f.read())
' "$TEMP_DIR"
fi

checksum_file="$OUTPUT_DIR/lucien-desktop_${VERSION}_SHA256SUMS"
(
  cd "$OUTPUT_DIR"
  sha256sum "${package_name}.zip" > "${package_name}.zip.sha256"
  # Um arquivo por plataforma construida, acrescentando em vez de sobrescrever:
  # cada host produz o seu, e os tres se juntam na pagina de Releases.
  touch "$(basename "$checksum_file")"
  grep -v -F "${package_name}.zip" "$(basename "$checksum_file")" \
    > "$(basename "$checksum_file").novo" || true
  cat "${package_name}.zip.sha256" >> "$(basename "$checksum_file").novo"
  mv "$(basename "$checksum_file").novo" "$(basename "$checksum_file")"
)

printf 'Package written to %s\n' "$archive"
printf 'Checksums: %s\n' "$checksum_file"
