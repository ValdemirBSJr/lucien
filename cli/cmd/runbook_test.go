package cmd

import (
	"bytes"
	"strings"
	"testing"
)

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
