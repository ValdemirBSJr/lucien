package main

import (
	"strings"
	"testing"
)

func TestParseTypedLogCampoVazioNaoGeraNada(t *testing.T) {
	pairs, plainText := parseTypedLog("   \n\t\n")
	if pairs != nil || plainText != "" {
		t.Fatalf("campo vazio deveria devolver nada: pairs=%+v plainText=%q", pairs, plainText)
	}
}

func TestParseTypedLogReconheceComandoComESemEspaco(t *testing.T) {
	pairs, plainText := parseTypedLog("\\@ ls -la\ndrwxr-xr-x pasta\n\\@pwd\n/home/valdemir")
	if plainText != "" {
		t.Fatalf("texto puro nao deveria aparecer junto de comandos: %q", plainText)
	}
	if len(pairs) != 2 {
		t.Fatalf("esperava 2 comandos, veio %d: %+v", len(pairs), pairs)
	}
	if pairs[0].Command != "ls -la" || pairs[0].Output != "drwxr-xr-x pasta" {
		t.Fatalf("primeiro par inesperado: %+v", pairs[0])
	}
	if pairs[1].Command != "pwd" || pairs[1].Output != "/home/valdemir" {
		t.Fatalf("segundo par inesperado (sem espaco apos \\@): %+v", pairs[1])
	}
}

func TestParseTypedLogComandoSemSaida(t *testing.T) {
	pairs, _ := parseTypedLog("\\@ comando1\n\\@ comando2")
	if len(pairs) != 2 {
		t.Fatalf("esperava 2 comandos, veio %d: %+v", len(pairs), pairs)
	}
	for _, par := range pairs {
		if par.Output != "" {
			t.Fatalf("comando sem saida deveria ficar vazio: %+v", par)
		}
	}
}

func TestParseTypedLogSemMarcadorViraTextoNormal(t *testing.T) {
	pairs, plainText := parseTypedLog("Passos manuais: abrir o painel e clicar em Salvar.")
	if pairs != nil {
		t.Fatalf("texto sem \\@ nao deveria virar comando: %+v", pairs)
	}
	if plainText != "Passos manuais: abrir o painel e clicar em Salvar." {
		t.Fatalf("texto normal inesperado: %q", plainText)
	}
}

func TestParseTypedLogIgnoraLinhasAntesDoPrimeiroMarcador(t *testing.T) {
	pairs, plainText := parseTypedLog("preambulo solto\n\\@ ls\nsaida")
	if plainText != "" {
		t.Fatalf("preambulo nao deveria virar texto normal quando ha marcador: %q", plainText)
	}
	if len(pairs) != 1 || pairs[0].Command != "ls" || pairs[0].Output != "saida" {
		t.Fatalf("par inesperado: %+v", pairs)
	}
}

func TestInsertPlainProcedureTextEncaixaAposCabecalho(t *testing.T) {
	template := []byte("# Nome\n\n## Objetivo\n\n...\n\n## Procedimento\n\n## Validação\n\n...\n")
	resultado := string(insertPlainProcedureText(template, "texto solto"))
	if !strings.Contains(resultado, "## Procedimento\n\ntexto solto\n\n## Validação") {
		t.Fatalf("texto nao foi encaixado corretamente: %s", resultado)
	}
}
