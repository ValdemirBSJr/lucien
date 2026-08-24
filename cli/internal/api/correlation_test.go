package api

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

// clienteContra devolve um Client apontado para um servidor de teste.
func clienteContra(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	base, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("interpretar URL: %v", err)
	}
	return &Client{baseURL: base, token: "token", http: server.Client()}
}

func erroDoHub(t *testing.T, handler http.HandlerFunc) *HTTPError {
	t.Helper()
	client := clienteContra(t, handler)
	_, err := client.Active(context.Background())
	if err == nil {
		t.Fatal("esperava erro do Hub")
	}
	httpError, ok := err.(*HTTPError)
	if !ok {
		t.Fatalf("esperava *HTTPError, veio %T", err)
	}
	return httpError
}

// O Hub gerava o identificador e o CLI o descartava: quem relatava uma falha
// so conseguia dizer o horario aproximado.
func TestErroDeDominioMostraOIdentificadorDaRequisicao(t *testing.T) {
	httpError := erroDoHub(t, func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusNotFound)
		_, _ = writer.Write([]byte(
			`{"detail":"job não encontrado","request_id":"a1b2c3d4e5f6a7b8"}`,
		))
	})

	if httpError.RequestID != "a1b2c3d4e5f6a7b8" {
		t.Fatalf("identificador perdido: %q", httpError.RequestID)
	}
	if !strings.Contains(httpError.Error(), "request_id: a1b2c3d4e5f6a7b8") {
		t.Fatalf("mensagem sem o identificador: %q", httpError.Error())
	}
}

// Recusa na borda -- credencial invalida, TLS ausente -- responde sem
// request_id no corpo, e e justamente o caso mais dificil de investigar. O
// cabecalho existe em toda resposta.
func TestRecusaNaBordaUsaOCabecalhoComoSegundaFonte(t *testing.T) {
	httpError := erroDoHub(t, func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("X-Request-Id", "0f9e8d7c6b5a4938")
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusUnauthorized)
		_, _ = writer.Write([]byte(`{"detail":"credencial inválida"}`))
	})

	if httpError.RequestID != "0f9e8d7c6b5a4938" {
		t.Fatalf("identificador perdido: %q", httpError.RequestID)
	}
}

// O valor chega pela rede e vai para o terminal do operador. Um Hub adulterado
// -- ou um proxy no meio -- nao pode injetar escape de terminal por aqui.
//
// A injecao vai pelo corpo JSON, e nao pelo cabecalho: o servidor HTTP do Go
// se recusa a transmitir caractere de controle em cabecalho, entao aquele
// caminho ja esta protegido antes de chegar em nos. O corpo nao esta.
func TestIdentificadorHostilNaoChegaAoTerminal(t *testing.T) {
	hostis := map[string]string{
		"escape de terminal": `\u001b[31mvermelho-para-sempre`,
		"quebra de linha":    `linha-um\nlinha-dois`,
		"retorno de carro":   `apaga-a-linha\r`,
		"curto demais":       "abc",
		"longo demais":       strings.Repeat("x", 65),
		"espaco":             "com espaco aqui",
		"ponto e virgula":    "id;rm -rf /",
	}
	for nome, valor := range hostis {
		t.Run(nome, func(t *testing.T) {
			corpo := `{"detail":"falhou","request_id":"` + valor + `"}`
			httpError := erroDoHub(t, func(writer http.ResponseWriter, _ *http.Request) {
				writer.Header().Set("Content-Type", "application/json")
				writer.WriteHeader(http.StatusInternalServerError)
				_, _ = writer.Write([]byte(corpo))
			})

			if httpError.RequestID != "" {
				t.Fatalf("aceitou identificador hostil: %q", httpError.RequestID)
			}
			if strings.Contains(httpError.Error(), "request_id") {
				t.Fatalf("mensagem cita identificador vazio: %q", httpError.Error())
			}
		})
	}
}

// Corpo hostil nao pode cegar a segunda fonte: o cabecalho continua valendo.
func TestCorpoHostilNaoDescartaOCabecalhoValido(t *testing.T) {
	httpError := erroDoHub(t, func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("X-Request-Id", "1a2b3c4d5e6f7a8b")
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusBadGateway)
		_, _ = writer.Write([]byte(`{"detail":"upstream","request_id":"nao serve"}`))
	})

	if httpError.RequestID != "1a2b3c4d5e6f7a8b" {
		t.Fatalf("caiu fora do cabecalho: %q", httpError.RequestID)
	}
}

// Um Hub anterior a esta versao nao devolve identificador nenhum. A mensagem
// precisa continuar legivel, sem sufixo pendurado.
func TestSemIdentificadorAMensagemNaoMuda(t *testing.T) {
	httpError := erroDoHub(t, func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusConflict)
		_, _ = writer.Write([]byte(`{"detail":"conteúdo divergente"}`))
	})

	if esperado := "Hub rejected the request: conteúdo divergente"; httpError.Error() != esperado {
		t.Fatalf("mensagem mudou: %q", httpError.Error())
	}
}
