package editor

import (
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"

	"github.com/mattn/go-shellwords"
)

const maxDraftBytes = 1024 * 1024

// Edit abre o rascunho de um playbook no $EDITOR do operador.
func Edit(initial []byte) ([]byte, error) {
	return EditWith(initial, "lucien-playbook-*.md", maxDraftBytes)
}

// EditWith e o mesmo fluxo com o sufixo e o teto de tamanho do chamador.
//
// A gravacao de uma sessao nao cabe no teto do playbook: o log local vai a
// 2 MiB por padrao e a 10 MiB com LUCIEN_MAX_LOG_BYTES. Reaproveitar o limite
// de 1 MiB recusaria justamente a sessao longa -- a que mais custa refazer.
func EditWith(initial []byte, pattern string, maxBytes int) ([]byte, error) {
	temporary, err := os.CreateTemp("", pattern)
	if err != nil {
		return nil, err
	}
	path := temporary.Name()
	defer os.Remove(path)

	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return nil, err
	}
	if _, err := temporary.Write(initial); err != nil {
		temporary.Close()
		return nil, err
	}
	if err := temporary.Close(); err != nil {
		return nil, err
	}

	editorCommand := os.Getenv("EDITOR")
	if editorCommand == "" {
		editorCommand = "vi"
	}
	arguments, err := shellwords.Parse(editorCommand)
	if err != nil || len(arguments) == 0 {
		return nil, errors.New("EDITOR is invalid")
	}
	// Não usamos `sh -c`: o caminho temporário nunca é interpretado por um shell.
	command := exec.Command(arguments[0], append(arguments[1:], path)...)
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Run(); err != nil {
		return nil, fmt.Errorf("editor exited with an error: %w", err)
	}

	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	content, err := io.ReadAll(io.LimitReader(file, int64(maxBytes)+1))
	if err != nil {
		return nil, err
	}
	if len(content) > maxBytes {
		return nil, fmt.Errorf("edited content exceeds %d bytes", maxBytes)
	}
	return content, nil
}
