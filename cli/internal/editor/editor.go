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

func Edit(initial []byte) ([]byte, error) {
	temporary, err := os.CreateTemp("", "lucien-playbook-*.md")
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
	content, err := io.ReadAll(io.LimitReader(file, maxDraftBytes+1))
	if err != nil {
		return nil, err
	}
	if len(content) > maxDraftBytes {
		return nil, errors.New("playbook exceeds 1 MiB")
	}
	return content, nil
}
