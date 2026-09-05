package recording

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// Um log recusado pela politica de segredo fica em disco ate alguem apaga-lo.
// Estes testes cobrem o unico caminho que passou a apaga-lo.

func prepararSessao(t *testing.T, status string) (Session, string) {
	t.Helper()
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	logPath := filepath.Join(t.TempDir(), "session.log")
	if err := os.WriteFile(logPath, []byte("enable secret exemplo\r\n"), 0o600); err != nil {
		t.Fatalf("criar log: %v", err)
	}
	session := Session{JobName: "job-local", LogPath: logPath, Status: status}
	if err := saveSession(session); err != nil {
		t.Fatalf("salvar sessão: %v", err)
	}
	return session, logPath
}

func TestPendingDistingueAusenciaDeFalha(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	// Sem sessão não é erro: é o caso normal do `start`, que precisa seguir
	// em frente. Confundir os dois faria toda gravação nova falhar.
	session, existe, err := Pending()
	if err != nil {
		t.Fatalf("ausência de sessão não devia ser erro: %v", err)
	}
	if existe {
		t.Fatalf("nenhuma sessão foi salva, mas Pending achou %q", session.JobName)
	}
}

func TestPendingDevolveSessaoAindaEmAndamento(t *testing.T) {
	// Ao contrário de PendingUpload, aceita RUNNING -- é assim que o `start`
	// descobre que sobrescreveria uma captura viva.
	esperada, _ := prepararSessao(t, "RUNNING")

	session, existe, err := Pending()
	if err != nil {
		t.Fatalf("Pending: %v", err)
	}
	if !existe || session.JobName != esperada.JobName {
		t.Fatalf("sessão RUNNING não foi devolvida: existe=%v session=%+v", existe, session)
	}
}

func TestDiscardApagaEstadoELog(t *testing.T) {
	_, logPath := prepararSessao(t, "STOPPED")

	session, existia, err := Discard()
	if err != nil {
		t.Fatalf("Discard: %v", err)
	}
	if !existia || session.JobName != "job-local" {
		t.Fatalf("Discard não devolveu o que removeu: existia=%v session=%+v", existia, session)
	}

	// O log é o ponto: o session.json seria sobrescrito por um `start` de
	// qualquer forma, mas o arquivo com o segredo só sai por aqui.
	if _, err := os.Stat(logPath); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("o log gravado sobreviveu ao discard: %v", err)
	}
	if _, existe, _ := Pending(); existe {
		t.Fatal("o estado da sessão sobreviveu ao discard")
	}
}

func TestDiscardSemSessaoNaoEErro(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	if _, existia, err := Discard(); err != nil || existia {
		t.Fatalf("discard sem sessão devia ser silencioso: existia=%v err=%v", existia, err)
	}
}

func TestLogRemoveANSIComoOUploadFaria(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	logPath := filepath.Join(t.TempDir(), "session.log")
	if err := os.WriteFile(logPath, []byte("\x1b[32mdocker ps\x1b[0m\r\n"), 0o600); err != nil {
		t.Fatalf("criar log: %v", err)
	}

	// `session cat` promete mostrar o que o upload enviaria. Se a limpeza
	// divergir, o operador lê uma coisa e o Hub recebe outra.
	log, err := Log(Session{LogPath: logPath})
	if err != nil {
		t.Fatalf("Log: %v", err)
	}
	if log != "docker ps\n" {
		t.Fatalf("saída não confere com a do upload: %q", log)
	}
}
