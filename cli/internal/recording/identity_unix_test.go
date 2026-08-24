//go:build !windows

package recording

import (
	"os"
	"testing"
)

func TestProcessIdentityDistingueProcessos(t *testing.T) {
	meu, ok := processIdentity(os.Getpid())
	if !ok || meu == "" {
		t.Fatal("o proprio processo deveria ter identidade")
	}
	// Estavel entre leituras: o instante de inicio nao muda.
	repetido, _ := processIdentity(os.Getpid())
	if repetido != meu {
		t.Fatalf("identidade instavel: %q e %q", meu, repetido)
	}
	pai, ok := processIdentity(os.Getppid())
	if ok && pai == meu {
		t.Fatal("processos distintos nao podem compartilhar identidade")
	}
	if _, ok := processIdentity(0); ok {
		t.Fatal("PID invalido nao tem identidade")
	}
	if _, ok := processIdentity(4194303); ok {
		t.Fatal("PID inexistente nao tem identidade")
	}
}

func TestOwnsProcessRecusaPIDReciclado(t *testing.T) {
	// O cenario real: entre `start` e `stop` houve reboot, e outro processo
	// ocupa o PID. Sinalizar aqui mataria alguem alheio.
	sessao := Session{
		PID:             os.Getpid(),
		ProcessIdentity: "999999:1",
	}
	if ownsProcess(sessao) {
		t.Fatal("identidade divergente deveria recusar a posse")
	}

	atual, _ := processIdentity(os.Getpid())
	sessao.ProcessIdentity = atual
	if !ownsProcess(sessao) {
		t.Fatal("identidade correta deveria confirmar a posse")
	}
}

func TestOwnsProcessAceitaSessaoAnteriorAoCampo(t *testing.T) {
	// Sem identidade gravada nao ha o que comparar. Recusar deixaria a sessao
	// presa, sem forma de encerra-la.
	sessao := Session{PID: os.Getpid()}
	if !ownsProcess(sessao) {
		t.Fatal("sessao antiga deveria manter o comportamento anterior")
	}
}

func TestOwnsProcessRecusaProcessoInexistente(t *testing.T) {
	if ownsProcess(Session{PID: 4194303, ProcessIdentity: "1:1"}) {
		t.Fatal("processo inexistente nao pode ser considerado nosso")
	}
}
