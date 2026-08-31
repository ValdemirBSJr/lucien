//go:build !windows

package recording

import (
	"fmt"
	"slices"
	"strings"
	"syscall"
	"testing"
)

func TestErroEIOAoFecharPTYEhEncerramentoEsperado(t *testing.T) {
	t.Parallel()

	if !isExpectedPTYCloseError(fmt.Errorf("read PTY: %w", syscall.EIO)) {
		t.Fatal("EIO ao fechar o PTY deve ser tratado como encerramento normal")
	}
}

func TestErroInesperadoDoPTYEhPreservado(t *testing.T) {
	t.Parallel()

	if isExpectedPTYCloseError(syscall.EINVAL) {
		t.Fatal("erro inesperado do PTY não pode ser ocultado")
	}
}

func TestTamanhoInicialDoPTYNuncaEhDegenerado(t *testing.T) {
	t.Parallel()

	// O kernel cria o PTY com 0x0 quando nenhum tamanho é informado. Comandos
	// locais ignoram isso, mas o ssh propaga as dimensões ao equipamento remoto
	// e uma OLT com zero linhas não desenha nada.
	size := initialWindowSize()
	if size == nil {
		t.Fatal("o PTY não pode ser criado sem tamanho")
	}
	if size.Rows == 0 || size.Cols == 0 {
		t.Fatalf("tamanho degenerado propagaria 0x0 ao remoto: %dx%d",
			size.Rows, size.Cols)
	}
}

func TestRecordingEnvironmentIsolaOHistorico(t *testing.T) {
	// O historico do operador entrava na gravacao pela seta para cima, e a
	// gravacao saia para o historico em texto puro ao encerrar o shell.
	ambiente := recordingEnvironment([]string{
		"PATH=/usr/bin",
		"HISTFILE=/home/operador/.bash_history",
		"SHELL=/bin/bash",
	})

	var histfile []string
	for _, entrada := range ambiente {
		if strings.HasPrefix(entrada, "HISTFILE=") {
			histfile = append(histfile, entrada)
		}
	}
	if len(histfile) != 1 || histfile[0] != "HISTFILE=/dev/null" {
		t.Fatalf("HISTFILE do shell gravado: %v", histfile)
	}

	// O resto do ambiente e o que faz a sessao parecer a do operador: alias,
	// PATH e prompt. Perder isso mudaria o procedimento que esta sendo gravado.
	for _, esperado := range []string{"PATH=/usr/bin", "SHELL=/bin/bash"} {
		if !slices.Contains(ambiente, esperado) {
			t.Fatalf("%q sumiu do ambiente: %v", esperado, ambiente)
		}
	}
}

func TestRecordingEnvironmentDefineHistfileQuandoNaoHavia(t *testing.T) {
	ambiente := recordingEnvironment([]string{"PATH=/usr/bin"})

	if !slices.Contains(ambiente, "HISTFILE=/dev/null") {
		t.Fatalf("HISTFILE ausente: %v", ambiente)
	}
}
