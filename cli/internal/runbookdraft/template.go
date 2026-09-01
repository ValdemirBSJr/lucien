// Package runbookdraft monta o modelo Markdown inicial de um runbook a
// partir dos comandos capturados e das sugestões da SLM. Vive em internal/
// porque tanto o CLI de terminal (cmd/job.go, via $EDITOR) quanto o app
// desktop (cli/desktop, via um editor gráfico) precisam do exato mesmo
// texto -- duplicar esta lógica arriscaria as duas superfícies divergirem
// silenciosamente na formatação bilíngue ou nos avisos de revisão
// obrigatória.
package runbookdraft

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/lucien-runbook/lucien/internal/api"
)

// generatedSessionSuffix reconhece o sufixo que `lucien start` acrescenta ao
// nome quando o operador não escolhe um -- carimbo de data/hora e um trecho
// aleatório, que não tem lugar no título de um runbook publicado.
var generatedSessionSuffix = regexp.MustCompile(`-\d{8}-\d{6}-[0-9a-f]{12}$`)

// DisplayName remove o sufixo gerado automaticamente, quando presente. Um
// nome escolhido manualmente pelo operador passa intacto.
func DisplayName(name string) string {
	return generatedSessionSuffix.ReplaceAllString(name, "")
}

// CommandStep é um comando capturado, já filtrado pelos que o operador
// confirmou como úteis, com a saída e o impacto sugerido que acompanham.
type CommandStep struct {
	Command string
	Output  string
	Impact  string
}

// SelectedCommandSteps filtra `commands` pelos itens em `selected`,
// preservando a ordem original e casando cada um com sua saída/impacto pelo
// mesmo índice.
func SelectedCommandSteps(
	commands []string, outputs []string, impacts []string, selected []string,
) []CommandStep {
	selectedSet := make(map[string]struct{}, len(selected))
	for _, command := range selected {
		selectedSet[command] = struct{}{}
	}

	steps := make([]CommandStep, 0, len(selected))
	for index, command := range commands {
		if _, included := selectedSet[command]; !included {
			continue
		}
		output := ""
		if index < len(outputs) {
			output = outputs[index]
		}
		impact := ""
		if index < len(impacts) {
			impact = impacts[index]
		}
		steps = append(steps, CommandStep{
			Command: command,
			Output:  output,
			Impact:  impact,
		})
	}
	return steps
}

// MarkdownTemplate monta o rascunho inicial: título, objetivo (sugerido pela
// SLM ou, na ausência dela, a descrição do operador), arquitetura e
// pré-requisitos, um passo por comando selecionado, validação e rollback.
// `language` aceita somente "pt-br" ou "en".
func MarkdownTemplate(
	name string,
	jobID string,
	steps []CommandStep,
	suggestions api.RunbookSuggestions,
	description string,
	language string,
) ([]byte, error) {
	var builder strings.Builder
	fmt.Fprintf(&builder, "# %s\n\n", name)
	fmt.Fprintf(&builder, "<!-- Job-ID: %s -->\n\n", jobID)

	var objective, architecture, procedure, action, explanation, validation, rollback string
	var suggestionNotice, commandReview, impactLabel, rollbackNotice string
	// A descrição vem do operador em `lucien start -d`, não da SLM; rotulá-la
	// como sugestão do modelo falsearia a procedência do texto.
	var descriptionNotice string
	switch language {
	case "pt-br":
		objective = "## Objetivo\n\nDescreva o objetivo operacional.\n\n"
		architecture = "## Arquitetura e pré-requisitos\n\nDescreva as dependências, os riscos e o rollback.\n\n"
		procedure = "## Procedimento\n\n"
		action = "Executar comando selecionado"
		explanation = "> Explique o objetivo, o impacto e o resultado esperado.\n\n"
		validation = "## Validação\n\nDescreva como verificar o resultado.\n\n"
		rollback = "## Rollback\n\nDescreva o procedimento seguro de rollback.\n"
		suggestionNotice = "> **REVISÃO OBRIGATÓRIA — SUGESTÃO DA SLM:** valide e ajuste antes da publicação.\n\n"
		commandReview = "> **REVISÃO OBRIGATÓRIA — COMANDO CAPTURADO:** não execute sem validar alvo, impacto, permissões e plano de retorno.\n\n"
		impactLabel = "> **REVISÃO OBRIGATÓRIA — SUGESTÃO DA SLM:** possível impacto: %s Valide antes de executar.\n\n"
		rollbackNotice = "> **REVISÃO OBRIGATÓRIA — SUGESTÃO DA SLM:** valide a segurança e a ordem antes de executar os comandos de rollback abaixo.\n\n"
		descriptionNotice = "> **REVISÃO OBRIGATÓRIA — DESCRIÇÃO DO OPERADOR:** texto informado na captura; complete o objetivo antes da publicação.\n\n"
	case "en":
		objective = "## Objective\n\nDescribe the operational objective.\n\n"
		architecture = "## Architecture and prerequisites\n\nDescribe dependencies, risks, and rollback.\n\n"
		procedure = "## Procedure\n\n"
		action = "Run selected command"
		explanation = "> Explain the objective, impact, and expected result.\n\n"
		validation = "## Validation\n\nDescribe how to verify the result.\n\n"
		rollback = "## Rollback\n\nDescribe the safe rollback procedure.\n"
		suggestionNotice = "> **MANDATORY REVIEW — SLM SUGGESTION:** validate and adjust before publication.\n\n"
		commandReview = "> **MANDATORY REVIEW — CAPTURED COMMAND:** do not execute before validating the target, impact, permissions, and recovery plan.\n\n"
		impactLabel = "> **MANDATORY REVIEW — SLM SUGGESTION:** possible impact: %s Validate before execution.\n\n"
		rollbackNotice = "> **MANDATORY REVIEW — SLM SUGGESTION:** validate safety and order before executing the rollback commands below.\n\n"
		descriptionNotice = "> **MANDATORY REVIEW — OPERATOR DESCRIPTION:** text provided at capture time; complete the objective before publication.\n\n"
	default:
		return nil, fmt.Errorf("unsupported runbook language %q", language)
	}

	switch {
	case strings.TrimSpace(suggestions.Objective) != "":
		builder.WriteString(strings.SplitN(objective, "\n\n", 2)[0] + "\n\n")
		builder.WriteString(suggestionNotice)
		fmt.Fprintf(&builder, "> %s\n\n", suggestions.Objective)
	case strings.TrimSpace(description) != "":
		// Sem enriquecimento, a descrição da captura é o único contexto que o
		// operador já escreveu; preservá-la evita reescrever do zero.
		//
		// Ela vira subtitulo, e nao citacao: e o assunto do runbook, e um
		// titulo torna o documento reconhecivel no indice da wiki e na
		// listagem do portal. A nota de revisao vem depois porque e ali que
		// o operador escreve o objetivo em si.
		builder.WriteString(strings.SplitN(objective, "\n\n", 2)[0] + "\n\n")
		fmt.Fprintf(&builder, "### %s\n\n", SingleLine(description))
		builder.WriteString(descriptionNotice)
	default:
		builder.WriteString(objective)
	}
	if len(suggestions.ArchitecturePrerequisites) == 0 {
		builder.WriteString(architecture)
	} else {
		builder.WriteString(strings.SplitN(architecture, "\n\n", 2)[0] + "\n\n")
		builder.WriteString(suggestionNotice)
		for _, item := range suggestions.ArchitecturePrerequisites {
			fmt.Fprintf(&builder, "- %s\n", item)
		}
		builder.WriteString("\n")
	}
	builder.WriteString(procedure)
	for index, step := range steps {
		builder.WriteString(commandReview)
		if strings.TrimSpace(step.Impact) != "" {
			fmt.Fprintf(&builder, impactLabel, step.Impact)
		}
		if language == "pt-br" {
			fmt.Fprintf(&builder, "### Passo %d: %s\n", index+1, action)
		} else {
			fmt.Fprintf(&builder, "### Step %d: %s\n", index+1, action)
		}
		builder.WriteString("```bash\n")
		builder.WriteString(step.Command)
		builder.WriteString("\n```\n")
		if strings.TrimSpace(step.Output) != "" {
			writeOutputBlock(&builder, step.Output)
		}
		if strings.TrimSpace(step.Impact) == "" {
			builder.WriteString(explanation)
		}
	}
	builder.WriteString(validation)
	if len(suggestions.RollbackCommands) == 0 {
		builder.WriteString(rollback)
	} else {
		builder.WriteString(strings.SplitN(rollback, "\n\n", 2)[0] + "\n\n")
		builder.WriteString(rollbackNotice)
		writeCodeBlock(&builder, "sh", strings.Join(suggestions.RollbackCommands, "\n"))
	}
	return []byte(builder.String()), nil
}

func writeOutputBlock(builder *strings.Builder, output string) {
	writeCodeBlock(builder, "text", output)
}

func writeCodeBlock(builder *strings.Builder, language string, content string) {
	// O fence cresce quando a saída contém crases, evitando quebrar o Markdown.
	fence := "```"
	for strings.Contains(content, fence) {
		fence += "`"
	}
	fmt.Fprintf(
		builder,
		"%s%s\n%s\n%s\n",
		fence,
		language,
		strings.TrimRight(content, "\r\n"),
		fence,
	)
}

// SingleLine achata a descricao para caber num cabecalho Markdown. O Hub ja
// normaliza os espacos, mas o rascunho e montado com o que o CLI recebeu:
// uma quebra de linha aqui partiria o titulo e o resto viraria texto solto.
func SingleLine(value string) string {
	return strings.Join(strings.Fields(value), " ")
}
