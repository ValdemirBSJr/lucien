//go:build !windows

package recording

import (
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/creack/pty"
	"golang.org/x/term"
)

// recordingEnvironment devolve o ambiente do shell gravado, com histórico
// próprio.
//
// O filho herdava o `HISTFILE` do operador, e isso vazava nos dois sentidos:
// uma seta para cima trazia um comando de antes da gravação para dentro do
// runbook, e ao sair o shell escrevia a sessão inteira no histórico em texto
// puro -- fora do alcance da sanitização do Hub.
//
// Nada é apagado. O histórico real do operador fica intacto, e a seta para cima
// continua funcionando dentro da sessão, porque o histórico corrente vive em
// memória. Um `.bashrc` que defina `HISTFILE` explicitamente ainda vence esta
// variável; por isso o Hub também descarta os comandos do próprio CLI.
func recordingEnvironment(base []string) []string {
	ambiente := make([]string, 0, len(base)+1)
	for _, entrada := range base {
		// Remover antes de acrescentar: duas definições da mesma variável
		// deixariam a escolha para o shell, que é quem não deveria escolher.
		if strings.HasPrefix(entrada, "HISTFILE=") {
			continue
		}
		ambiente = append(ambiente, entrada)
	}
	return append(ambiente, "HISTFILE=/dev/null")
}

func Start(provision, description, domainFunction string) (Session, error) {
	if !validProvision.MatchString(provision) {
		return Session{}, errors.New("provider name must use 1-48 characters: ASCII letters (no accents), numbers, dot, hyphen, or underscore -- it becomes a file name in the published repository")
	}
	path, err := sessionPath()
	if err != nil {
		return Session{}, err
	}
	if _, err := os.Stat(path); err == nil {
		// Mesmo STOPPED pode conter um log ainda não enviado; sobrescrever perderia auditoria.
		existing, loadErr := loadSession()
		if loadErr != nil {
			return Session{}, fmt.Errorf("load existing session: %w", loadErr)
		}
		if existing.Status == "STOPPED" {
			return Session{}, errors.New("a session is already waiting for upload; run lucien upload")
		}
		return Session{}, errors.New("a session is already running; run lucien stop")
	} else if !errors.Is(err, os.ErrNotExist) {
		return Session{}, fmt.Errorf("check existing session: %w", err)
	}

	stateDirectory, err := configStateDirectory()
	if err != nil {
		return Session{}, err
	}
	if err := os.MkdirAll(filepath.Join(stateDirectory, "logs"), 0o700); err != nil {
		return Session{}, err
	}
	logFile, err := os.CreateTemp(filepath.Join(stateDirectory, "logs"), "session-*.log")
	if err != nil {
		return Session{}, err
	}
	defer logFile.Close()
	if err := logFile.Chmod(0o600); err != nil {
		return Session{}, err
	}

	shell := os.Getenv("SHELL")
	if shell == "" {
		shell = "/bin/sh"
	}
	child := exec.Command(shell)
	child.Env = recordingEnvironment(os.Environ())
	terminal, err := pty.StartWithSize(child, initialWindowSize())
	if err != nil {
		return Session{}, fmt.Errorf("start PTY: %w", err)
	}
	defer terminal.Close()
	if restoreTerminal, err := prepareInteractiveTerminal(terminal); err != nil {
		_ = child.Process.Kill()
		_, _ = child.Process.Wait()
		return Session{}, err
	} else {
		defer restoreTerminal()
	}

	session, err := newSession(
		provision, description, domainFunction, child.Process.Pid, logFile.Name(),
	)
	if err != nil {
		_ = child.Process.Kill()
		return Session{}, err
	}
	if err := saveSession(session); err != nil {
		_ = child.Process.Kill()
		return Session{}, err
	}

	go func() { _, _ = io.Copy(terminal, os.Stdin) }()
	recorder := &cappedWriter{file: logFile, remaining: maxLogBytes()}
	_, copyErr := io.Copy(io.MultiWriter(os.Stdout, recorder), terminal)
	waitErr := child.Wait()
	session.PID = 0
	session.Status = "STOPPED"
	session.LogTruncated = recorder.truncated
	if err := saveSession(session); err != nil {
		return Session{}, err
	}
	if !isExpectedPTYCloseError(copyErr) {
		return Session{}, copyErr
	}
	// SIGTERM enviado por `stop` é término esperado, não falha operacional.
	if waitErr != nil {
		var exitError *exec.ExitError
		if !errors.As(waitErr, &exitError) {
			return Session{}, waitErr
		}
	}
	return session, nil
}

// initialWindowSize evita que o PTY nasça 0x0, que é o padrão do kernel quando
// nenhum tamanho é informado. Comandos locais ignoram as dimensões, mas o ssh
// as propaga ao equipamento remoto: uma OLT ou um CMTS que recebe 0 linhas não
// desenha nada e a sessão parece congelada.
func initialWindowSize() *pty.Winsize {
	if size, err := pty.GetsizeFull(os.Stdin); err == nil &&
		size.Rows > 0 && size.Cols > 0 {
		return size
	}
	// Sem terminal de origem (pipe, cron), 80x24 mantém o remoto utilizável.
	return &pty.Winsize{Rows: 24, Cols: 80}
}

func prepareInteractiveTerminal(terminal *os.File) (func(), error) {
	stdinFD := int(os.Stdin.Fd())
	if !term.IsTerminal(stdinFD) {
		return func() {}, nil
	}

	previousState, err := term.MakeRaw(stdinFD)
	if err != nil {
		return nil, fmt.Errorf("prepare interactive terminal: %w", err)
	}
	inheritValidSize(terminal)

	resizeSignal := make(chan os.Signal, 1)
	stopResize := make(chan struct{})
	signal.Notify(resizeSignal, syscall.SIGWINCH)
	go func() {
		for {
			select {
			case <-resizeSignal:
				inheritValidSize(terminal)
			case <-stopResize:
				return
			}
		}
	}()

	return func() {
		signal.Stop(resizeSignal)
		close(stopResize)
		_ = term.Restore(stdinFD, previousState)
	}, nil
}

// inheritValidSize ignora dimensões degeneradas do terminal de origem. Alguns
// emuladores reportam 0x0 antes do primeiro SIGWINCH; propagar isso ao PTY
// desfaria o tamanho inicial e voltaria a quebrar o ssh para equipamentos.
func inheritValidSize(terminal *os.File) {
	size, err := pty.GetsizeFull(os.Stdin)
	if err != nil || size.Rows == 0 || size.Cols == 0 {
		return
	}
	_ = pty.Setsize(terminal, size)
}

func isExpectedPTYCloseError(err error) bool {
	return err == nil || errors.Is(err, os.ErrClosed) || errors.Is(err, syscall.EIO)
}

func configStateDirectory() (string, error) {
	return stateDirectory()
}
