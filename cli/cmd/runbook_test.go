package cmd

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"github.com/lucien-runbook/lucien/internal/api"
)

func TestRunbookListEstaRegistradoESemArgumentos(t *testing.T) {
	t.Parallel()

	var encontrado bool
	for _, sub := range newRunbookCommand().Commands() {
		if sub.Name() != "list" {
			continue
		}
		encontrado = true
		if err := sub.Args(sub, []string{"extra"}); err == nil {
			t.Fatal("runbook list deveria recusar argumento posicional")
		}
	}
	if !encontrado {
		t.Fatal("runbook list nao foi registrado em newRunbookCommand")
	}
}

func TestCurrentRunbooksDeixaSoAPontaEOrdenaPorAreaENome(t *testing.T) {
	// A versao superada sai: o revise a recusaria com conflito. A ordem e a
	// de quem procura -- area, depois nome, e o ID so desempata.
	t.Parallel()

	obtidos := currentRunbooks([]api.PublishedRunbookSummary{
		{ID: "c", Name: "zeta", Area: "servers", Latest: true},
		{ID: "a", Name: "alfa", Area: "servers", Latest: false},
		{ID: "b", Name: "alfa", Area: "servers", Latest: true},
		{ID: "d", Name: "beta", Area: "access", Latest: true},
	})

	var ids []string
	for _, runbook := range obtidos {
		ids = append(ids, runbook.ID)
	}
	if strings.Join(ids, ",") != "d,b,c" {
		t.Fatalf("ordem ou filtro inesperado: %v", ids)
	}
}

func TestWriteRunbookListMostraColunasEPreencheOQueFalta(t *testing.T) {
	t.Parallel()

	var saida, dica bytes.Buffer
	err := writeRunbookList(&saida, &dica, []api.PublishedRunbookSummary{
		{
			ID:          "3e381ebe-0284-4d3b-b304-a13655e3dd4c",
			Name:        "check-default-route",
			Area:        "servers",
			PublishedAt: time.Date(2026, 9, 8, 10, 35, 0, 0, time.Local),
			Latest:      true,
		},
		// Como chega de um Hub anterior ao campo `runbooks`.
		{ID: "52d1b673-06f4-45ac-96db-73a5a9cf11c0", Latest: true},
	})
	if err != nil {
		t.Fatalf("escrita falhou: %v", err)
	}

	linhas := strings.Split(strings.TrimRight(saida.String(), "\n"), "\n")
	if len(linhas) != 3 {
		t.Fatalf("esperava cabecalho e duas linhas, veio: %q", saida.String())
	}
	for _, coluna := range []string{"NAME", "ID", "AREA", "PUBLISHED AT"} {
		if !strings.Contains(linhas[0], coluna) {
			t.Fatalf("cabecalho sem %q: %q", coluna, linhas[0])
		}
	}
	for _, trecho := range []string{"check-default-route", "3e381ebe-0284-4d3b-b304-a13655e3dd4c", "servers", "2026-09-08 10:35"} {
		if !strings.Contains(linhas[1], trecho) {
			t.Fatalf("linha sem %q: %q", trecho, linhas[1])
		}
	}
	if strings.Count(linhas[2], "—") != 3 {
		t.Fatalf("nome, area e data ausentes deveriam virar traco: %q", linhas[2])
	}
	// A dica vai para o stderr: o stdout fica so a tabela.
	if strings.Contains(saida.String(), "lucien runbook revise") {
		t.Fatal("a dica vazou para o stdout")
	}
	if !strings.Contains(dica.String(), "lucien runbook revise <ID>") {
		t.Fatalf("dica ausente no stderr: %q", dica.String())
	}
}

func TestWriteRunbookListVazioDizOQueAconteceu(t *testing.T) {
	t.Parallel()

	var saida, dica bytes.Buffer
	if err := writeRunbookList(&saida, &dica, nil); err != nil {
		t.Fatalf("escrita falhou: %v", err)
	}
	if !strings.Contains(saida.String(), "No published runbook in your areas.") {
		t.Fatalf("mensagem de lista vazia ausente: %q", saida.String())
	}
	if dica.Len() != 0 {
		t.Fatalf("sem runbook nao ha o que revisar; dica inesperada: %q", dica.String())
	}
}

func TestReviseExigeUUIDCanonico(t *testing.T) {
	t.Parallel()

	// O comando aceita uma unica forma: nem indice de reviews, nem nome.
	aceitos := []string{
		"3e381ebe-0284-4d3b-b304-a13655e3dd4c",
		"52d1b673-06f4-45ac-96db-73a5a9cf11c0",
	}
	for _, id := range aceitos {
		if !canonicalRunbookID.MatchString(id) {
			t.Fatalf("UUID canonico deveria ser aceito: %q", id)
		}
	}

	recusados := []string{
		"1",
		"",
		"validar-placa-olt-hw",
		"validar-placa-olt-hw-20260817-234823-f198bde04ad1",
		"3E381EBE-0284-4D3B-B304-A13655E3DD4C",
		"3e381ebe02844d3bb304a13655e3dd4c",
		"3e381ebe-0284-4d3b-b304-a13655e3dd4",
		" 3e381ebe-0284-4d3b-b304-a13655e3dd4c",
	}
	for _, valor := range recusados {
		if canonicalRunbookID.MatchString(valor) {
			t.Fatalf("valor nao canonico foi aceito: %q", valor)
		}
	}
}

func TestRunbookCatRecusaIndiceENome(t *testing.T) {
	// A recusa tem de vir do proprio comando, antes de qualquer rede. Sem
	// API_HOST configurado um caminho que fale com o Hub falharia com outra
	// mensagem -- entao a mensagem exata e a prova de que parou aqui.
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	for _, entrada := range []string{"1", "validar-placa-olt-hw", "3e381ebe"} {
		comando := newRunbookCatCommand()
		comando.SetOut(&bytes.Buffer{})
		comando.SetErr(&bytes.Buffer{})
		comando.SetArgs([]string{entrada})

		err := comando.Execute()
		if err == nil {
			t.Fatalf("esperava recusa para %q", entrada)
		}
		if !strings.Contains(err.Error(), "exact published runbook UUID") {
			t.Fatalf("para %q, mensagem inesperada: %v", entrada, err)
		}
	}
}

func TestRunbookCatEReviseCobramAMesmaFormaDeID(t *testing.T) {
	// Os dois operam sobre a mesma publicacao imutavel. Aceitar formas
	// diferentes de identificador faria o operador conferir um runbook e
	// revisar outro.
	t.Parallel()

	if newRunbookCatCommand().Use != "cat <published_runbook_uuid>" {
		t.Fatalf("assinatura do cat mudou: %q", newRunbookCatCommand().Use)
	}
	if newRunbookReviseCommand().Use != "revise <published_runbook_uuid>" {
		t.Fatalf("assinatura do revise mudou: %q", newRunbookReviseCommand().Use)
	}
}

func TestRunbookCatEstaRegistradoNoComandoRunbook(t *testing.T) {
	t.Parallel()

	var encontrado bool
	for _, sub := range newRunbookCommand().Commands() {
		if sub.Name() == "cat" {
			encontrado = true
		}
	}
	if !encontrado {
		t.Fatal("runbook cat nao foi registrado em newRunbookCommand")
	}
}
