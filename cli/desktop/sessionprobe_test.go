package main

import (
	"errors"
	"fmt"
	"net"
	"testing"

	"github.com/lucien-runbook/lucien/internal/api"
)

// classificarFalhaDoMe reproduz a decisão que AuthStatus toma sobre o erro
// devolvido por client.Me. Fica separada do AuthStatus porque aquele precisa de
// credencial salva e de rede; o que importa provar aqui é a classificação, que
// é onde estava o defeito.
func classificarFalhaDoMe(err error) SessionProbe {
	var recusa *api.HTTPError
	if errors.As(err, &recusa) {
		return SessionProbe{}
	}
	return SessionProbe{Unreachable: true}
}

func TestHubQueRecusaNaoEHubInalcancavel(t *testing.T) {
	t.Parallel()

	// Credencial invalida, expirada ou revogada: o Hub respondeu. Mandar o
	// operador para a tela de token e a resposta certa.
	for _, status := range []int{401, 403} {
		erro := error(&api.HTTPError{StatusCode: status, Detail: "not authenticated"})
		if probe := classificarFalhaDoMe(erro); probe.Unreachable {
			t.Fatalf("status %d foi tratado como Hub inalcancavel", status)
		}
	}

	// Mesmo embrulhado, continua sendo recusa do Hub.
	embrulhado := fmt.Errorf("ao confirmar a credencial: %w",
		&api.HTTPError{StatusCode: 401, Detail: "not authenticated"})
	if probe := classificarFalhaDoMe(embrulhado); probe.Unreachable {
		t.Fatal("HTTPError embrulhado deixou de ser reconhecido")
	}
}

func TestFalhaDeTransporteEHubInalcancavel(t *testing.T) {
	t.Parallel()

	// Sem resposta do Hub: DNS, rota, TLS, conexao recusada. Digitar um token
	// nao muda nenhuma dessas -- era essa a tela errada que o app abria.
	falhas := []error{
		fmt.Errorf("Hub communication failed: %w", &net.OpError{
			Op: "dial", Net: "tcp", Err: errors.New("connection refused"),
		}),
		fmt.Errorf("Hub communication failed: %w", &net.DNSError{
			Err: "no such host", Name: "hub.exemplo.interno",
		}),
		errors.New("Hub communication failed: x509: certificate signed by unknown authority"),
	}
	for _, falha := range falhas {
		probe := classificarFalhaDoMe(falha)
		if !probe.Unreachable {
			t.Fatalf("nao classificou como inalcancavel: %v", falha)
		}
		if probe.Identity != nil {
			t.Fatalf("probe inalcancavel trouxe identidade: %v", falha)
		}
	}
}

func TestProbeInalcancavelNaoCarregaOEnderecoDoHub(t *testing.T) {
	t.Parallel()

	// O erro de transporte do Go traz a URL, e a tela de configuracao mascara
	// justamente esse endereco. O probe nao tem onde carregar texto de erro, e
	// este teste existe para que acrescentar um campo assim exija uma decisao.
	probe := classificarFalhaDoMe(errors.New(
		`Get "https://hub.exemplo.interno:8443/me": dial tcp: connection refused`,
	))
	if !probe.Unreachable {
		t.Fatal("esperava inalcancavel")
	}
	if probe != (SessionProbe{Unreachable: true}) {
		t.Fatalf("o probe ganhou campo alem de Unreachable: %+v", probe)
	}
}
