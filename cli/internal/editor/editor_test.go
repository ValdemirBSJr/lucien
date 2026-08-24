package editor

import (
	"os"
	"runtime"
	"strings"
	"testing"
)

func TestEditInvocaEditorSemManterArquivoTemporario(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("teste usa shell POSIX do container oficial")
	}
	oldEditor := os.Getenv("EDITOR")
	t.Cleanup(func() { _ = os.Setenv("EDITOR", oldEditor) })
	if err := os.Setenv("EDITOR", `sh -c 'printf "# revisado\\n" > "$1"' lucien-editor`); err != nil {
		t.Fatal(err)
	}
	content, err := Edit([]byte("# inicial\n"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(content)) != "# revisado" {
		t.Fatalf("conteúdo inesperado: %q", content)
	}
}
