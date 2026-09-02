package main

import (
	"strings"
	"testing"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/runbookdraft"
)

// montarComoOApp reproduz o que GenerateLocalDraft faz com o texto digitado,
// sem a parte que fala com o Hub (a leitura do idioma).
func montarComoOApp(t *testing.T, name, description, rawLog string) string {
	t.Helper()

	pairs, plainText := parseTypedLog(rawLog)
	steps := make([]runbookdraft.CommandStep, len(pairs))
	for index, pair := range pairs {
		steps[index] = runbookdraft.CommandStep{Command: pair.Command, Output: pair.Output}
	}
	template, err := runbookdraft.MarkdownTemplate(
		runbookdraft.DisplayName(name), "", steps, api.RunbookSuggestions{},
		description, "pt-br",
	)
	if err != nil {
		t.Fatalf("montar o modelo: %v", err)
	}
	if plainText != "" {
		template = insertPlainProcedureText(template, plainText)
	}
	return string(template)
}

// temPassoOperacional espelha o que o Hub exige em
// backend/app/domain/publication.py: pelo menos um "### Passo N: Ação", e ele
// precisa ser seguido de um bloco bash ou documentar uma imagem.
func temPassoOperacional(markdown string) bool {
	linhas := strings.Split(markdown, "\n")
	for i, linha := range linhas {
		if !strings.HasPrefix(linha, "### Passo ") && !strings.HasPrefix(linha, "### Step ") {
			continue
		}
		if i+1 < len(linhas) && linhas[i+1] == "```bash" {
			return true
		}
		for j := i + 1; j < len(linhas) && !strings.HasPrefix(linhas[j], "### "); j++ {
			if strings.Contains(linhas[j], "![") {
				return true
			}
		}
	}
	return false
}

func TestTextoSemMarcadorGeraRascunhoQueOHubRecusa(t *testing.T) {
	// O caso relatado em producao: o operador cola o procedimento em prosa,
	// sem nenhum \@. parseTypedLog devolve plainText, steps sai vazio, e o
	// modelo nasce sem passo nenhum -- a publicacao so falha la no Hub, com
	// "the playbook must contain at least one operational step".
	markdown := montarComoOApp(t, "Trocar placa", "Substituicao de placa",
		"Abri o chassi\nTroquei a placa do slot 3\nConferi os LEDs")

	if temPassoOperacional(markdown) {
		t.Fatal("o rascunho tem passo: o defeito relatado nao se reproduz mais")
	}
	if !strings.Contains(markdown, "Abri o chassi") {
		t.Fatal("o texto digitado nao entrou no rascunho")
	}
}

func TestTextoComMarcadorGeraRascunhoPublicavel(t *testing.T) {
	// O mesmo procedimento, com o marcador: nasce com passo e o Hub aceita.
	markdown := montarComoOApp(t, "Trocar placa", "Substituicao de placa",
		"\\@ display board\nBoard 3 present\n\\@ reset slot 3\nOK")

	if !temPassoOperacional(markdown) {
		t.Fatalf("esperava passo operacional, e o rascunho saiu sem:\n%s", markdown)
	}
}

func TestCampoVazioTambemNasceSemPasso(t *testing.T) {
	// Runbook puramente visual: valido para o Hub so depois de o operador
	// acrescentar um passo com imagem no editor. Sem isso, mesma recusa.
	markdown := montarComoOApp(t, "Configurar portal", "", "")

	if temPassoOperacional(markdown) {
		t.Fatal("rascunho vazio nasceu com passo")
	}
}
