//go:build !windows

package recording

import (
	"fmt"
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
