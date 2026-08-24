//go:build !windows

package recording

import (
	"os"
	"testing"
)

func TestInsideSessionReconheceAscendencia(t *testing.T) {
	// O proprio processo conta: `lucien stop` comparado com ele mesmo nao
	// acontece na pratica, mas ancora a leitura da cadeia.
	if !insideSession(os.Getpid()) {
		t.Fatal("o proprio PID deveria ser reconhecido")
	}
	pai, ok := parentPID(os.Getpid())
	if !ok || pai != os.Getppid() {
		t.Fatalf("parentPID retornou (%d, %t), esperado (%d, true)", pai, ok, os.Getppid())
	}
	// Em conteineres, o processo de teste pode ser filho direto do PID 1. O
	// contrato de insideSession recusa PID 1 de proposito; fora desse caso, o
	// pai precisa ser reconhecido na cadeia.
	if pai > 1 && !insideSession(pai) {
		t.Fatal("o processo pai deveria ser reconhecido")
	}
}

func TestInsideSessionRecusaProcessoNaoRelacionado(t *testing.T) {
	// PID 1 e ancestral de tudo, mas nao e uma sessao do Lucien: a busca para
	// antes dele de proposito, senao todo `stop` se calaria.
	if insideSession(1) {
		t.Fatal("PID 1 nao pode ser tratado como sessao")
	}
	if insideSession(0) || insideSession(-5) {
		t.Fatal("PID invalido deveria ser recusado")
	}
	// Um PID que quase certamente nao existe nem e ancestral.
	if insideSession(4194303) {
		t.Fatal("PID inexistente foi tratado como ancestral")
	}
}
