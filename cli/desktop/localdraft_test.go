package main

import "testing"

func TestLocalDraftRoundtrip(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	app := &App{}
	original := LocalDraft{
		Markdown: "# titulo\n\ntexto",
		Assets: []EditorAsset{
			{Filename: "img-1.png", ContentBase64: "AAAA", MediaType: "image/png"},
		},
	}
	if err := app.SaveLocalDraft("job-1", original); err != nil {
		t.Fatalf("salvar rascunho: %v", err)
	}

	loaded, err := app.LoadLocalDraft("job-1")
	if err != nil {
		t.Fatalf("carregar rascunho: %v", err)
	}
	if loaded.Markdown != original.Markdown {
		t.Fatalf("markdown divergente: %q", loaded.Markdown)
	}
	if len(loaded.Assets) != 1 || loaded.Assets[0].Filename != "img-1.png" {
		t.Fatalf("assets divergentes: %+v", loaded.Assets)
	}

	if err := app.DeleteLocalDraft("job-1"); err != nil {
		t.Fatalf("apagar rascunho: %v", err)
	}
	afterDelete, err := app.LoadLocalDraft("job-1")
	if err != nil {
		t.Fatalf("carregar apos apagar: %v", err)
	}
	if afterDelete.Markdown != "" || len(afterDelete.Assets) != 0 {
		t.Fatalf("rascunho deveria estar vazio apos apagar: %+v", afterDelete)
	}
}

func TestLocalDraftSemNadaSalvoNaoEErro(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	app := &App{}
	draft, err := app.LoadLocalDraft("job-nunca-editado")
	if err != nil {
		t.Fatalf("carregar rascunho inexistente nao deveria falhar: %v", err)
	}
	if draft.Markdown != "" || draft.Assets != nil {
		t.Fatalf("esperava rascunho vazio: %+v", draft)
	}
}

func TestLocalDraftIsolaPorJobID(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	app := &App{}
	if err := app.SaveLocalDraft("job-a", LocalDraft{Markdown: "a"}); err != nil {
		t.Fatalf("salvar job-a: %v", err)
	}
	if err := app.SaveLocalDraft("job-b", LocalDraft{Markdown: "b"}); err != nil {
		t.Fatalf("salvar job-b: %v", err)
	}

	loadedA, err := app.LoadLocalDraft("job-a")
	if err != nil {
		t.Fatalf("carregar job-a: %v", err)
	}
	loadedB, err := app.LoadLocalDraft("job-b")
	if err != nil {
		t.Fatalf("carregar job-b: %v", err)
	}
	if loadedA.Markdown != "a" || loadedB.Markdown != "b" {
		t.Fatalf("rascunhos vazaram entre jobs: a=%q b=%q", loadedA.Markdown, loadedB.Markdown)
	}
}
