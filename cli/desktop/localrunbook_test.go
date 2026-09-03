package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLocalRunbookRoundtrip(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	app := &App{}
	salvo, err := app.SaveLocalRunbook(LocalRunbook{
		Name:           "trocar-certificado",
		Description:    "renovacao anual",
		DomainFunction: "servidores",
		RawLog:         "\\@ openssl x509 -noout -dates\nnotAfter=...",
		Markdown:       "### Passo 1: Conferir validade\n",
		Assets: []EditorAsset{
			{Filename: "img-1.png", ContentBase64: "AAAA", MediaType: "image/png"},
		},
	})
	if err != nil {
		t.Fatalf("salvar: %v", err)
	}
	if salvo.ID == "" {
		t.Fatal("o id devia ter sido gerado na primeira gravacao")
	}
	if salvo.CreatedAt == "" {
		t.Fatal("a data devia ter sido gerada na primeira gravacao")
	}

	lido, err := app.LoadLocalRunbook(salvo.ID)
	if err != nil {
		t.Fatalf("carregar: %v", err)
	}
	// O RawLog e o campo que o "Enriquecer" consome. Se ele nao sobreviver ao
	// disco, retomar um rascunho salvo deixa o botao sem entrada.
	if lido.RawLog != salvo.RawLog {
		t.Fatalf("raw log divergente: %q", lido.RawLog)
	}
	if lido.Markdown != salvo.Markdown || lido.DomainFunction != "servidores" {
		t.Fatalf("registro divergente: %+v", lido)
	}
	if len(lido.Assets) != 1 || lido.Assets[0].Filename != "img-1.png" {
		t.Fatalf("assets divergentes: %+v", lido.Assets)
	}

	if err := app.DeleteLocalRunbook(salvo.ID); err != nil {
		t.Fatalf("apagar: %v", err)
	}
	if _, err := app.LoadLocalRunbook(salvo.ID); err == nil {
		t.Fatal("carregar depois de apagar devia falhar")
	}
}

func TestSalvarDeNovoPreservaIDEData(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	app := &App{}
	primeiro, err := app.SaveLocalRunbook(LocalRunbook{Name: "rascunho"})
	if err != nil {
		t.Fatalf("primeira gravacao: %v", err)
	}
	primeiro.Markdown = "### Passo 1: Editado depois\n"
	segundo, err := app.SaveLocalRunbook(primeiro)
	if err != nil {
		t.Fatalf("segunda gravacao: %v", err)
	}

	// Reeditar nao pode criar um segundo rascunho nem "rejuvenescer" a data:
	// a lista e ordenada por ela, e o item pularia de lugar a cada tecla.
	if segundo.ID != primeiro.ID || segundo.CreatedAt != primeiro.CreatedAt {
		t.Fatalf("id ou data mudaram ao reeditar: %+v -> %+v", primeiro, segundo)
	}
	lista, err := app.ListLocalRunbooks()
	if err != nil {
		t.Fatalf("listar: %v", err)
	}
	if len(lista) != 1 {
		t.Fatalf("esperava 1 rascunho, veio %d", len(lista))
	}
	if lista[0].Markdown != "### Passo 1: Editado depois\n" {
		t.Fatalf("a lista trouxe versao antiga: %q", lista[0].Markdown)
	}
}

func TestListaOrdenaDoMaisNovoParaOMaisAntigo(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	app := &App{}
	for _, momento := range []string{
		"2026-09-01T10:00:00Z", "2026-09-03T10:00:00Z", "2026-09-02T10:00:00Z",
	} {
		if _, err := app.SaveLocalRunbook(
			LocalRunbook{Name: momento, CreatedAt: momento},
		); err != nil {
			t.Fatalf("salvar %s: %v", momento, err)
		}
	}

	lista, err := app.ListLocalRunbooks()
	if err != nil {
		t.Fatalf("listar: %v", err)
	}
	if len(lista) != 3 {
		t.Fatalf("esperava 3, veio %d", len(lista))
	}
	if lista[0].Name != "2026-09-03T10:00:00Z" || lista[2].Name != "2026-09-01T10:00:00Z" {
		t.Fatalf("ordem errada: %s, %s, %s", lista[0].Name, lista[1].Name, lista[2].Name)
	}
}

func TestListaVaziaSemDiretorio(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	// Primeira execucao do app: o diretorio nao existe. Isso e o estado
	// inicial normal, nao uma falha -- a tela de Ativos precisa abrir.
	lista, err := (&App{}).ListLocalRunbooks()
	if err != nil {
		t.Fatalf("listar sem diretorio: %v", err)
	}
	if len(lista) != 0 {
		t.Fatalf("esperava lista vazia, veio %d", len(lista))
	}
}

func TestArquivoCorrompidoNaoEscondeOsOutros(t *testing.T) {
	state := t.TempDir()
	t.Setenv("XDG_STATE_HOME", state)

	app := &App{}
	bom, err := app.SaveLocalRunbook(LocalRunbook{Name: "intacto"})
	if err != nil {
		t.Fatalf("salvar: %v", err)
	}
	dir := filepath.Join(state, "lucien", "local-runbooks")
	if err := os.WriteFile(
		filepath.Join(dir, "quebrado.json"), []byte("{isto nao e json"), 0o600,
	); err != nil {
		t.Fatalf("escrever arquivo corrompido: %v", err)
	}

	lista, err := app.ListLocalRunbooks()
	if err != nil {
		t.Fatalf("listar: %v", err)
	}
	if len(lista) != 1 || lista[0].ID != bom.ID {
		t.Fatalf("um arquivo ilegivel escondeu os demais: %+v", lista)
	}
}

func TestIDInvalidoNaoViraCaminho(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	app := &App{}
	// O id vira nome de arquivo. Um valor com travessia precisa ser recusado
	// antes de tocar o disco, e nao sanitizado silenciosamente.
	for _, id := range []string{
		"../../../etc/passwd", "nao-e-uuid", "", "..",
	} {
		if _, err := app.LoadLocalRunbook(id); err == nil {
			t.Fatalf("carregar aceitou id invalido: %q", id)
		}
		if err := app.DeleteLocalRunbook(id); err == nil {
			t.Fatalf("apagar aceitou id invalido: %q", id)
		}
	}
}

func TestIDGeradoTemFormaDeUUIDv4(t *testing.T) {
	primeiro, err := novoIDLocal()
	if err != nil {
		t.Fatalf("gerar id: %v", err)
	}
	if !localRunbookID.MatchString(primeiro) {
		t.Fatalf("id fora do formato esperado: %q", primeiro)
	}
	if !strings.HasPrefix(primeiro[14:], "4") {
		t.Fatalf("versao do UUID deveria ser 4: %q", primeiro)
	}
	segundo, err := novoIDLocal()
	if err != nil {
		t.Fatalf("gerar segundo id: %v", err)
	}
	if primeiro == segundo {
		t.Fatal("dois ids gerados em sequencia colidiram")
	}
}
