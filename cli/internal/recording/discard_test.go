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

func TestReplaceLogTrocaAGravacaoPreservandoOEstado(t *testing.T) {
	session, logPath := prepararSessao(t, "STOPPED")

	if err := ReplaceLog(session, "docker ps\n"); err != nil {
		t.Fatalf("ReplaceLog: %v", err)
	}

	conteudo, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("ler log: %v", err)
	}
	if string(conteudo) != "docker ps\n" {
		t.Fatalf("gravação não foi substituída: %q", conteudo)
	}
	// O estado tem que sobreviver: é ele que liga o log ao nome do Job, e um
	// upload posterior depende disso.
	if _, existe, _ := Pending(); !existe {
		t.Fatal("o estado da sessão sumiu ao trocar o log")
	}
}

func TestUploadEnviaOTextoCorrigido(t *testing.T) {
	session, _ := prepararSessao(t, "STOPPED")

	// Este é o ponto do `session edit`: o upload relê o log do disco a cada
	// tentativa, então corrigir e reenviar tem que funcionar sem gravar de
	// novo. Sem isto, uma recusa por um segredo custaria a sessão inteira.
	//
	// O texto corrigido é neutro de propósito. A primeira versão deste teste
	// usava uma fixture com forma de credencial de equipamento, para parecer
	// com o caso real, e o portão de segredos reprovou o PR: a própria fixture
	// casava a regra. O que o teste prova é que o upload pega a substituição
	// -- para isso o conteúdo não precisa ter forma de segredo.
	if err := ReplaceLog(session, "show version\n"); err != nil {
		t.Fatalf("ReplaceLog: %v", err)
	}

	_, log, err := PendingUpload()
	if err != nil {
		t.Fatalf("PendingUpload: %v", err)
	}
	if log != "show version\n" {
		t.Fatalf("o upload não pegou a correção: %q", log)
	}
}

func TestReplaceLogNaoDeixaTemporarioParaTras(t *testing.T) {
	session, logPath := prepararSessao(t, "STOPPED")

	if err := ReplaceLog(session, "ls -la\n"); err != nil {
		t.Fatalf("ReplaceLog: %v", err)
	}

	entradas, err := os.ReadDir(filepath.Dir(logPath))
	if err != nil {
		t.Fatalf("listar diretório: %v", err)
	}
	for _, entrada := range entradas {
		if len(entrada.Name()) > 0 && entrada.Name()[0] == '.' {
			t.Fatalf("temporário sobrou no diretório: %s", entrada.Name())
		}
	}
}
