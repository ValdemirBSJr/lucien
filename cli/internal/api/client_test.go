package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
)

func TestRetryJobUsaEndpointAssincrono(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.URL.EscapedPath() != "/jobs/job%20um/retry" {
			t.Fatalf("requisição inesperada: %s %s", request.Method, request.URL.EscapedPath())
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(Job{ID: "job-id", Status: "PROCESSING"})
	}))
	defer server.Close()

	base, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("interpretar URL: %v", err)
	}
	client := &Client{baseURL: base, token: "token", http: server.Client()}
	job, err := client.RetryJob(context.Background(), "job um", false)
	if err != nil {
		t.Fatalf("retry retornou erro: %v", err)
	}
	if job.Status != "PROCESSING" {
		t.Fatalf("status inesperado: %q", job.Status)
	}
}

func TestActiveListaFilaOperacional(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet || request.URL.EscapedPath() != "/jobs/active" {
			t.Fatalf("requisição inesperada: %s %s", request.Method, request.URL.EscapedPath())
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode([]Job{
			{ID: "processing-id", Status: "PROCESSING"},
			{ID: "pending-id", Status: "PENDING"},
			{ID: "failed-id", Status: "FAILED"},
		})
	}))
	defer server.Close()

	base, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("interpretar URL: %v", err)
	}
	client := &Client{baseURL: base, token: "token", http: server.Client()}
	jobs, err := client.Active(context.Background())
	if err != nil {
		t.Fatalf("listar fila ativa: %v", err)
	}
	if len(jobs) != 3 {
		t.Fatalf("quantidade inesperada de Jobs: %d", len(jobs))
	}
}

func TestRunbookConfigurationUsaEndpointAutenticado(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet || request.URL.EscapedPath() != "/configuration/runbook" {
			t.Fatalf("requisição inesperada: %s %s", request.Method, request.URL.EscapedPath())
		}
		if request.Header.Get("Authorization") != "Bearer token" {
			t.Fatal("credencial não foi enviada")
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(RunbookConfiguration{Language: "pt-br"})
	}))
	defer server.Close()

	base, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("interpretar URL: %v", err)
	}
	client := &Client{baseURL: base, token: "token", http: server.Client()}
	configuration, err := client.RunbookConfiguration(context.Background())
	if err != nil {
		t.Fatalf("obter configuração: %v", err)
	}
	if configuration.Language != "pt-br" {
		t.Fatalf("idioma inesperado: %q", configuration.Language)
	}
}

func TestDeleteJobForceEnviaCancelamentoExplicito(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodDelete || request.URL.EscapedPath() != "/jobs/job-id" {
			t.Fatalf("requisição inesperada: %s %s", request.Method, request.URL.EscapedPath())
		}
		if request.URL.Query().Get("force") != "true" {
			t.Fatal("cancelamento forçado não foi enviado")
		}
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	base, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("interpretar URL: %v", err)
	}
	client := &Client{baseURL: base, token: "token", http: server.Client()}
	if err := client.DeleteJob(context.Background(), "job-id", true); err != nil {
		t.Fatalf("cancelar Job: %v", err)
	}
}

func TestEndpointEscapaIdentificadorSemDuplaCodificacao(t *testing.T) {
	base, err := url.Parse("https://hub.exemplo.interno:8443")
	if err != nil {
		t.Fatalf("interpretar URL base: %v", err)
	}
	client := &Client{baseURL: base}

	got := client.endpoint("jobs", "redis cache/01", "publish")
	want := "https://hub.exemplo.interno:8443/jobs/redis%20cache%2F01/publish"
	if got != want {
		t.Fatalf("endpoint inesperado: %q != %q", got, want)
	}
}

func TestEndpointPreservaPrefixoDoHub(t *testing.T) {
	base, err := url.Parse("https://hub.exemplo.interno:8443/api")
	if err != nil {
		t.Fatalf("interpretar URL base: %v", err)
	}
	client := &Client{baseURL: base}

	got := client.endpoint("jobs", "pending")
	want := "https://hub.exemplo.interno:8443/api/jobs/pending"
	if got != want {
		t.Fatalf("endpoint inesperado: %q != %q", got, want)
	}
}

func TestEndpointDeRotacaoAceitaUsername(t *testing.T) {
	base, err := url.Parse("https://hub.exemplo.interno:8443")
	if err != nil {
		t.Fatalf("interpretar URL base: %v", err)
	}
	client := &Client{baseURL: base}

	got := client.endpoint("admin", "users", "operador.rede", "provisional-token")
	want := "https://hub.exemplo.interno:8443/admin/users/operador.rede/provisional-token"
	if got != want {
		t.Fatalf("endpoint inesperado: %q != %q", got, want)
	}
}

func TestTrocaTokenProvisorioRepeteComMesmaChave(t *testing.T) {
	var idempotencyKeys []string
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/auth/exchange" {
			t.Fatalf("caminho inesperado: %s", request.URL.Path)
		}
		if request.Header.Get("Authorization") != "Bearer luc_tmp_provisorio" {
			t.Fatalf("Authorization inesperado")
		}
		idempotencyKeys = append(idempotencyKeys, request.Header.Get("Idempotency-Key"))
		writer.Header().Set("Content-Type", "application/json")
		if len(idempotencyKeys) == 1 {
			_, _ = writer.Write([]byte("{"))
			return
		}
		_ = json.NewEncoder(writer).Encode(CreatedUser{
			ID:             "user-1",
			Username:       "operador",
			RoleLevel:      "junior",
			DomainFunction: "servidores",
			IsActive:       true,
			APIToken:       "luc_permanente",
		})
	}))
	defer server.Close()

	base, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("interpretar URL: %v", err)
	}
	client := &Client{baseURL: base, token: "luc_tmp_provisorio", http: server.Client()}

	issued, err := client.ExchangeProvisionalToken(t.Context())
	if err != nil {
		t.Fatalf("troca retornou erro: %v", err)
	}
	if issued.APIToken != "luc_permanente" {
		t.Fatalf("token permanente inesperado: %q", issued.APIToken)
	}
	if len(idempotencyKeys) != 2 || idempotencyKeys[0] == "" || idempotencyKeys[0] != idempotencyKeys[1] {
		t.Fatalf("retry não preservou a chave idempotente: %#v", idempotencyKeys)
	}
}
