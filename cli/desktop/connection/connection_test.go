package connection

import "testing"

// useTempConfigDir substitui userConfigDir por um diretorio temporario, sem
// depender de qual variavel de ambiente o SO local realmente le.
func useTempConfigDir(t *testing.T) {
	t.Helper()
	directory := t.TempDir()
	original := userConfigDir
	userConfigDir = func() (string, error) { return directory, nil }
	t.Cleanup(func() { userConfigDir = original })
}

func TestLoadSemArquivoDevolveVazioSemErro(t *testing.T) {
	useTempConfigDir(t)
	settings, err := Load()
	if err != nil {
		t.Fatalf("load sem configuracao previa nao deveria falhar: %v", err)
	}
	if settings.APIHost != "" || settings.CAFile != "" {
		t.Fatalf("esperava Settings vazio, recebeu %+v", settings)
	}
}

func TestSaveRecusaCamposVazios(t *testing.T) {
	useTempConfigDir(t)
	if err := Save(Settings{APIHost: "https://hub.local"}); err == nil {
		t.Fatal("esperava erro com CAFile vazio")
	}
	if err := Save(Settings{CAFile: "/tmp/ca.pem"}); err == nil {
		t.Fatal("esperava erro com APIHost vazio")
	}
}

func TestSaveELoadFazemRoundtrip(t *testing.T) {
	useTempConfigDir(t)
	original := Settings{APIHost: "https://hub.exemplo.interno:8443", CAFile: "/etc/lucien/ca.pem"}
	if err := Save(original); err != nil {
		t.Fatalf("save: %v", err)
	}
	loaded, err := Load()
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if loaded != original {
		t.Fatalf("roundtrip divergiu: gravado %+v, lido %+v", original, loaded)
	}
}
