package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// O guard do `start` roda antes de abrir o PTY. Estes testes exercitam só ele:
// abrir um PTY de verdade num teste não é possível, e o que precisa de rede de
// proteção é a decisão de apagar, não a captura.

func prepararEstado(t *testing.T, status, jobName string) string {
	t.Helper()
	stateDir := t.TempDir()
	t.Setenv("XDG_STATE_HOME", stateDir)

	logPath := filepath.Join(t.TempDir(), "session.log")
	if err := os.WriteFile(logPath, []byte("enable secret exemplo\n"), 0o600); err != nil {
		t.Fatalf("criar log: %v", err)
	}
	sessionJSON := `{"job_name":"` + jobName + `","log_path":"` +
		strings.ReplaceAll(logPath, `\`, `\\`) + `","status":"` + status + `"}`
	destino := filepath.Join(stateDir, "lucien")
	if err := os.MkdirAll(destino, 0o700); err != nil {
		t.Fatalf("criar diretório de estado: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(destino, "session.json"), []byte(sessionJSON), 0o600,
	); err != nil {
		t.Fatalf("salvar sessão: %v", err)
	}
	return logPath
}

func comandoDeTeste() (*cobra.Command, *bytes.Buffer) {
	saida := &bytes.Buffer{}
	command := &cobra.Command{}
	command.SetOut(saida)
	command.SetErr(saida)
	return command, saida
}

func TestStartSemSessaoPendenteNaoInterrompe(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	command, _ := comandoDeTeste()

	if err := clearPendingSession(command, false); err != nil {
		t.Fatalf("sem sessão pendente o start não devia parar: %v", err)
	}
}

func TestStartRecusaSobrescreverCapturaViva(t *testing.T) {
	prepararEstado(t, "RUNNING", "job-vivo")
	command, _ := comandoDeTeste()

	// Mesmo com -y: sobrescrever aqui deixaria o PTY rodando sem nada que o
	// descrevesse, e nem `stop` o encontraria depois.
	err := clearPendingSession(command, true)
	if err == nil || !strings.Contains(err.Error(), "lucien stop") {
		t.Fatalf("esperava recusa apontando para lucien stop, veio: %v", err)
	}
}

func TestStartComYesApagaSessaoOrfaELog(t *testing.T) {
	logPath := prepararEstado(t, "STOPPED", "job-orfao")
	command, _ := comandoDeTeste()

	if err := clearPendingSession(command, true); err != nil {
		t.Fatalf("clearPendingSession: %v", err)
	}

	// Este é o ponto da mudança: antes, o `start` sobrescrevia o session.json
	// e o log ficava órfão em disco, com o segredo dentro.
	if _, err := os.Stat(logPath); !os.IsNotExist(err) {
		t.Fatalf("o log da sessão órfã sobreviveu ao start: %v", err)
	}
}

func TestSessionCatRecusaDentroDeCaptura(t *testing.T) {
	// Fora de uma captura o comando roda; o guard só dispara com uma sessão
	// RUNNING cujo PID seja este processo. Aqui basta garantir que a ausência
	// de sessão dá o erro de "não há sessão", e não um pânico.
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	command := newSessionCatCommand()
	saida := &bytes.Buffer{}
	command.SetOut(saida)
	command.SetErr(saida)

	err := command.RunE(command, nil)
	if err == nil || !strings.Contains(err.Error(), "no local session found") {
		t.Fatalf("esperava 'no local session found', veio: %v", err)
	}
}

func TestSessionDiscardRecusaSessaoEmAndamento(t *testing.T) {
	prepararEstado(t, "RUNNING", "job-vivo")
	command := newSessionDiscardCommand()
	saida := &bytes.Buffer{}
	command.SetOut(saida)
	command.SetErr(saida)

	err := command.RunE(command, nil)
	if err == nil || !strings.Contains(err.Error(), "lucien stop") {
		t.Fatalf("esperava recusa apontando para lucien stop, veio: %v", err)
	}
}

func TestSessionEditRecusaSessaoEmAndamento(t *testing.T) {
	prepararEstado(t, "RUNNING", "job-vivo")
	command := newSessionEditCommand()
	saida := &bytes.Buffer{}
	command.SetOut(saida)
	command.SetErr(saida)

	// Abrir o editor sobre uma captura viva editaria o arquivo que ainda está
	// recebendo escrita do PTY.
	err := command.RunE(command, nil)
	if err == nil || !strings.Contains(err.Error(), "lucien stop") {
		t.Fatalf("esperava recusa apontando para lucien stop, veio: %v", err)
	}
}

func TestSessionEditSemSessaoNaoAbreEditor(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	command := newSessionEditCommand()
	saida := &bytes.Buffer{}
	command.SetOut(saida)
	command.SetErr(saida)

	err := command.RunE(command, nil)
	if err == nil || !strings.Contains(err.Error(), "no local session found") {
		t.Fatalf("esperava 'no local session found', veio: %v", err)
	}
}

func TestSessionCatMostraOndeOLogEsta(t *testing.T) {
	logPath := prepararEstado(t, "STOPPED", "job-parado")
	command := newSessionCatCommand()
	saidaPadrao := &bytes.Buffer{}
	saidaErro := &bytes.Buffer{}
	command.SetOut(saidaPadrao)
	command.SetErr(saidaErro)

	if err := command.RunE(command, nil); err != nil {
		t.Fatalf("session cat: %v", err)
	}
	// Sem o caminho, quem quer corrigir à mão não sabe qual arquivo abrir.
	if !strings.Contains(saidaErro.String(), logPath) {
		t.Fatalf("o cabeçalho não trouxe o caminho do log: %q", saidaErro.String())
	}
	if !strings.Contains(saidaErro.String(), "session edit") {
		t.Fatalf("o cabeçalho não aponta a saída construtiva: %q", saidaErro.String())
	}
	// A gravação sai no stdout, separada do cabeçalho, para aceitar pipe.
	if !strings.Contains(saidaPadrao.String(), "enable secret exemplo") {
		t.Fatalf("a gravação não saiu no stdout: %q", saidaPadrao.String())
	}
}
