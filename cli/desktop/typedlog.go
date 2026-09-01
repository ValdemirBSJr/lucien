package main

import "strings"

// typedLogPair é um par comando/saída reconhecido no texto digitado à mão no
// campo "Log bruto ou comandos" do modal de novo runbook -- não vem do Hub,
// então fica só no desktop, sem tocar em internal/runbookdraft (usado também
// pelo CLI de terminal, que nunca vê essa sintaxe).
type typedLogPair struct {
	Command string
	Output  string
}

// parseTypedLog interpreta o marcador \@: uma linha começando com \@ (com ou
// sem espaço depois) é um comando; as linhas seguintes, até o próximo \@ ou
// o fim do texto, são a saída dele. Sem nenhum \@ em nenhuma linha, o texto
// inteiro volta como parágrafo comum -- nunca os dois ao mesmo tempo.
func parseTypedLog(rawLog string) (pairs []typedLogPair, plainText string) {
	trimmed := strings.TrimSpace(rawLog)
	if trimmed == "" {
		return nil, ""
	}

	lines := strings.Split(rawLog, "\n")
	temMarcador := false
	for _, linha := range lines {
		if _, ok := typedLogCommand(linha); ok {
			temMarcador = true
			break
		}
	}
	if !temMarcador {
		return nil, trimmed
	}

	var atual *typedLogPair
	var saida []string
	flush := func() {
		if atual != nil {
			atual.Output = strings.TrimSpace(strings.Join(saida, "\n"))
			pairs = append(pairs, *atual)
		}
	}
	for _, linha := range lines {
		if comando, ok := typedLogCommand(linha); ok {
			flush()
			atual = &typedLogPair{Command: comando}
			saida = nil
			continue
		}
		if atual != nil {
			saida = append(saida, linha)
		}
		// Linhas antes do primeiro \@ não pertencem a comando nenhum --
		// "toda linha é comando ou saída de comando" pressupõe que o
		// marcador já apareceu.
	}
	flush()
	return pairs, ""
}

// typedLogCommand reconhece "\@ comando" e "\@comando" -- o espaço depois do
// marcador é opcional.
func typedLogCommand(line string) (string, bool) {
	trimmed := strings.TrimLeft(line, " \t")
	const marcador = `\@`
	if !strings.HasPrefix(trimmed, marcador) {
		return "", false
	}
	return strings.TrimSpace(strings.TrimPrefix(trimmed, marcador)), true
}

// insertPlainProcedureText encaixa um parágrafo comum logo abaixo do
// cabeçalho "## Procedimento"/"## Procedure" que o runbookdraft já gerou --
// usado quando o campo não tinha nenhum \@, e o texto inteiro vira prosa
// solta em vez de comando.
func insertPlainProcedureText(template []byte, plainText string) []byte {
	content := string(template)
	for _, cabecalho := range []string{"## Procedimento\n\n", "## Procedure\n\n"} {
		indice := strings.Index(content, cabecalho)
		if indice == -1 {
			continue
		}
		posicao := indice + len(cabecalho)
		return []byte(content[:posicao] + plainText + "\n\n" + content[posicao:])
	}
	return template
}
