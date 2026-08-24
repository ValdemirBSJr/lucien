package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/zalando/go-keyring"
)

func configureTestDirectory(t *testing.T) string {
	t.Helper()
	directory := t.TempDir()
	t.Setenv("APPDATA", directory)
	t.Setenv("XDG_CONFIG_HOME", directory)
	return directory
}

func TestTokenFicaNoKeyringENaoNoPerfil(t *testing.T) {
	configureTestDirectory(t)
	keyring.MockInit()
	profile := Profile{UserID: "user-1", Username: "alice"}
	token := "luc_token-super-secreto"

	if err := SaveAuthenticatedProfile(profile, "https://hub:8443", token); err != nil {
		t.Fatalf("SaveAuthenticatedProfile() erro = %v", err)
	}
	path, err := profilePath()
	if err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(content), token) || strings.Contains(string(content), "api_key") {
		t.Fatal("profile.json contém credencial em texto claro")
	}

	loaded, loadedToken, err := LoadAuthenticatedProfile("https://hub:8443")
	if err != nil {
		t.Fatalf("LoadAuthenticatedProfile() erro = %v", err)
	}
	if loaded.Username != "alice" || loadedToken != token {
		t.Fatalf("perfil/token divergente: %#v %q", loaded, loadedToken)
	}
}

func TestPerfilLegadoEMigradoSemManterToken(t *testing.T) {
	configureTestDirectory(t)
	keyring.MockInit()
	path, err := profilePath()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	legacy := []byte(`{"user_id":"legacy-1","name":"bob","api_key":"luc_legado"}`)
	if err := os.WriteFile(path, legacy, 0o600); err != nil {
		t.Fatal(err)
	}

	profile, token, err := LoadAuthenticatedProfile("https://hub:8443")
	if err != nil {
		t.Fatalf("migrar perfil: %v", err)
	}
	if profile.Username != "bob" || token != "luc_legado" {
		t.Fatalf("migração divergente: %#v %q", profile, token)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(content), "luc_legado") || strings.Contains(string(content), "api_key") {
		t.Fatal("token legado permaneceu em profile.json")
	}
}
