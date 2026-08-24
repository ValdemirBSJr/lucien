package api

import "testing"

func TestValidateAPIHostAceitaOrigem(t *testing.T) {
	for _, entrada := range []string{
		"https://lucien-api.interno:8443",
		"https://lucien-api.interno:8443/",
		"https://10.0.0.9",
	} {
		origem, err := ValidateAPIHost(entrada)
		if err != nil {
			t.Fatalf("origem valida recusada %q: %v", entrada, err)
		}
		// Normaliza sem a barra final: `JoinPath` produziria caminho duplo.
		if origem.Path != "" {
			t.Fatalf("origem manteve caminho %q", origem.Path)
		}
	}
}

func TestValidateAPIHostRecusaCredencialNaURL(t *testing.T) {
	// Credencial na URL vira nome de conta no keyring e vaza em qualquer
	// lugar que registre o endereco.
	if _, err := ValidateAPIHost("https://usuario:senha@hub:8443"); err == nil {
		t.Fatal("userinfo deveria ser recusado")
	}
	if _, err := ValidateAPIHost("https://usuario@hub:8443"); err == nil {
		t.Fatal("userinfo sem senha deveria ser recusado") // gitleaks:allow
	}
}

func TestValidateAPIHostRecusaCaminhoQueryEFragmento(t *testing.T) {
	// Qualquer um deles deslocaria os endpoints em silencio, e o erro
	// apareceria como 404 do Hub em vez de configuracao invalida.
	for _, entrada := range []string{
		"https://hub:8443/api",
		"https://hub:8443/api/v1/",
		"https://hub:8443?debug=1",
		"https://hub:8443/#secao",
		"https://hub:8443/caminho?x=1#y",
	} {
		if _, err := ValidateAPIHost(entrada); err == nil {
			t.Fatalf("entrada deveria ser recusada: %q", entrada)
		}
	}
}

func TestValidateAPIHostExigeHTTPSComHost(t *testing.T) {
	for _, entrada := range []string{
		"http://hub:8443",
		"hub:8443",
		"https://",
		"",
		" https://hub:8443",
		"https://hub:8443 ",
	} {
		if _, err := ValidateAPIHost(entrada); err == nil {
			t.Fatalf("entrada deveria ser recusada: %q", entrada)
		}
	}
}
