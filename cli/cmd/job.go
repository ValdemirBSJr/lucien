package cmd

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/AlecAivazis/survey/v2"
	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/draft"
	"github.com/lucien-runbook/lucien/internal/editor"
	"github.com/spf13/cobra"
)

var generatedSessionSuffix = regexp.MustCompile(`-\d{8}-\d{6}-[0-9a-f]{12}$`)

type activeJobLister interface {
	Active(context.Context) ([]api.Job, error)
}

// singleLine achata a descricao para caber num cabecalho Markdown. O Hub ja
// normaliza os espacos, mas o rascunho e montado com o que o CLI recebeu:
// uma quebra de linha aqui partiria o titulo e o resto viraria texto solto.
func singleLine(value string) string {
	return strings.Join(strings.Fields(value), " ")
}

func newJobCommand() *cobra.Command {
	var reiniciar bool
	jobCommand := &cobra.Command{
		Use:   "job <id_or_name_or_review_index>",
		Short: "Selects commands and opens the playbook in $EDITOR",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			identifier, err := resolveJobIdentifier(
				command.Context(), client, args[0],
			)
			if err != nil {
				return err
			}
			job, err := client.GetJob(command.Context(), identifier)
			if err != nil {
				return err
			}
			if job.Status != "PENDING" {
				return fmt.Errorf("job %s is not PENDING", job.ID)
			}
			// Um rascunho existente e trabalho humano ja feito. Regenerar o
			// modelo por cima dele apagaria a revisao inteira -- e e
			// exatamente o que acontece depois de um `job sent` recusado,
			// quando o operador roda `lucien job <id>` de novo para corrigir.
			anterior, erroRascunho := draft.Load(job.ID)
			retomando := erroRascunho == nil && len(anterior) > 0 && !reiniciar
			var partida []byte
			if retomando {
				fmt.Fprintf(
					command.ErrOrStderr(),
					"Resuming the saved draft for job %s. "+
						"Use --reset to discard it and start from the template.\n",
					job.ID,
				)
				partida = anterior
			} else {
				configuration, err := client.RunbookConfiguration(command.Context())
				if err != nil {
					return err
				}

				// Default controla apenas as marcações visuais. A resposta deve começar
				// vazia, pois o survey acrescenta nela os itens confirmados.
				selected := make([]string, 0, len(job.Commands))
				prompt := &survey.MultiSelect{
					Message: "Select the useful commands:",
					Options: job.Commands,
					Default: job.Commands,
				}
				if err := survey.AskOne(prompt, &selected); err != nil {
					return err
				}
				steps := selectedCommandSteps(
					job.Commands,
					job.CommandOutputs,
					job.RunbookSuggestions.CommandImpacts,
					selected,
				)
				partida, err = markdownTemplate(
					displayJobName(job.Name),
					job.ID,
					steps,
					job.RunbookSuggestions,
					job.Description,
					configuration.Language,
				)
				if err != nil {
					return err
				}
			}
			content, err := editor.Edit(partida)
			if err != nil {
				return err
			}
			// O comando `sent` será outro processo; persiste o rascunho com modo 0600.
			if err := draft.Save(job.ID, content); err != nil {
				return fmt.Errorf("save draft: %w", err)
			}
			fmt.Fprintf(command.OutOrStdout(), "Draft for job %s saved locally.\n", job.ID)
			return nil
		},
	}
	jobCommand.Flags().BoolVar(
		&reiniciar, "reset", false,
		"discards the saved draft and starts again from the template",
	)
	jobCommand.AddCommand(
		newJobSentCommand(),
		newJobDeleteCommand(),
		newJobStatusCommand(),
		newJobRetryCommand(),
	)
	return jobCommand
}

func displayJobName(name string) string {
	return generatedSessionSuffix.ReplaceAllString(name, "")
}

func parseJobIndex(identifier string) (int, bool) {
	index, err := strconv.Atoi(identifier)
	return index, err == nil
}

func resolveJobIndex(identifier string, jobs []api.Job) (string, error) {
	index, numeric := parseJobIndex(identifier)
	if !numeric {
		return identifier, nil
	}
	if len(jobs) == 0 {
		return "", errors.New("the reviews list is empty")
	}
	if index < 1 || index > len(jobs) {
		return "", fmt.Errorf("job index %d is outside the reviews list (1-%d)", index, len(jobs))
	}
	return jobs[index-1].ID, nil
}

func resolveJobIdentifier(
	ctx context.Context, client activeJobLister, identifier string,
) (string, error) {
	if _, numeric := parseJobIndex(identifier); !numeric {
		return identifier, nil
	}
	jobs, err := client.Active(ctx)
	if err != nil {
		return "", err
	}
	return resolveJobIndex(identifier, jobs)
}

func newJobStatusCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "status <id_or_name_or_review_index>",
		Short: "Shows asynchronous processing status",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			identifier, err := resolveJobIdentifier(
				command.Context(), client, args[0],
			)
			if err != nil {
				return err
			}
			job, err := client.GetJob(command.Context(), identifier)
			if err != nil {
				return err
			}
			fmt.Fprintf(command.OutOrStdout(), "ID: %s\nName: %s\nStatus: %s\n", job.ID, job.Name, job.Status)
			if job.ProcessingError != "" {
				fmt.Fprintf(command.OutOrStdout(), "Error: %s\n", job.ProcessingError)
			}
			return nil
		},
	}
}

func newJobRetryCommand() *cobra.Command {
	var skipEnrichment bool
	retryCommand := &cobra.Command{
		Use:   "retry <id_or_name_or_review_index>",
		Short: "Retries a FAILED asynchronous job",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			identifier, err := resolveJobIdentifier(
				command.Context(), client, args[0],
			)
			if err != nil {
				return err
			}
			job, err := client.GetJob(command.Context(), identifier)
			if err != nil {
				return err
			}
			if err := validateRetryableJob(job); err != nil {
				return err
			}
			job, err = client.RetryJob(command.Context(), job.ID, skipEnrichment)
			if err != nil {
				return err
			}
			fmt.Fprintf(
				command.OutOrStdout(),
				"Job %s queued again. Check progress with: lucien job status %s\n",
				job.ID,
				job.ID,
			)
			return nil
		},
	}
	retryCommand.Flags().BoolVarP(
		&skipEnrichment,
		"skip-enrichment",
		"s",
		false,
		"Reprocesses without the SLM enrichment step; omit to keep the original upload's choice",
	)
	return retryCommand
}

func validateRetryableJob(job api.Job) error {
	if job.Status != "FAILED" {
		return fmt.Errorf(
			"job %s is %s; retry is allowed only for FAILED jobs",
			job.ID,
			job.Status,
		)
	}
	return nil
}

func newJobSentCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "sent <id_or_name_or_review_index>",
		Short: "Publishes the reviewed draft",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			client, profile, err := activeClient()
			if err != nil {
				return err
			}
			identifier, err := resolveJobIdentifier(
				command.Context(), client, args[0],
			)
			if err != nil {
				return err
			}
			job, err := client.GetJob(command.Context(), identifier)
			if err != nil {
				return err
			}
			content, err := draft.Load(job.ID)
			if err != nil {
				return err
			}
			idempotency := sha256.Sum256([]byte(profile.UserID + "\x00" + job.ID + "\x00" + string(content)))
			published, err := client.Publish(
				command.Context(), job.ID, string(content), hex.EncodeToString(idempotency[:]),
			)
			if err != nil {
				return err
			}
			if err := draft.Delete(job.ID); err != nil {
				return fmt.Errorf("published, but the local draft was not removed: %w", err)
			}
			if published.SanitizationCount > 0 {
				fmt.Fprintf(
					command.OutOrStdout(),
					"Warning: the Hub replaced %d sensitive value(s) with instructional placeholders.\n",
					published.SanitizationCount,
				)
			}
			fmt.Fprintf(command.OutOrStdout(), "Job %s published to %s.\n", published.ID, published.StorageURL)
			return nil
		},
	}
}

func newJobDeleteCommand() *cobra.Command {
	var assumeYes bool
	var force bool
	deleteCommand := &cobra.Command{
		Use:   "del <id_or_name_or_review_index>",
		Short: "Deletes a PENDING or FAILED job; --force also cancels PROCESSING",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			identifier, err := resolveJobIdentifier(
				command.Context(), client, args[0],
			)
			if err != nil {
				return err
			}
			if !assumeYes {
				confirmed := false
				if err := survey.AskOne(&survey.Confirm{
					Message: fmt.Sprintf("Permanently delete job %s?", args[0]),
					Default: false,
				}, &confirmed); err != nil {
					return err
				}
				if !confirmed {
					return nil
				}
			}
			job, err := client.GetJob(command.Context(), identifier)
			if err != nil {
				return err
			}
			if err := client.DeleteJob(command.Context(), job.ID, force); err != nil {
				return err
			}
			_ = draft.Delete(job.ID)
			fmt.Fprintf(command.OutOrStdout(), "Job %s deleted.\n", job.ID)
			return nil
		},
	}
	deleteCommand.Flags().BoolVarP(&assumeYes, "yes", "y", false, "skip confirmation")
	deleteCommand.Flags().BoolVarP(
		&force,
		"force",
		"f",
		false,
		"cancel and delete a PROCESSING job; never deletes PUBLISHED jobs",
	)
	return deleteCommand
}

type commandStep struct {
	command string
	output  string
	impact  string
}

func selectedCommandSteps(
	commands []string, outputs []string, impacts []string, selected []string,
) []commandStep {
	selectedSet := make(map[string]struct{}, len(selected))
	for _, command := range selected {
		selectedSet[command] = struct{}{}
	}

	steps := make([]commandStep, 0, len(selected))
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
		steps = append(steps, commandStep{
			command: command,
			output:  output,
			impact:  impact,
		})
	}
	return steps
}

func markdownTemplate(
	name string,
	jobID string,
	steps []commandStep,
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
		fmt.Fprintf(&builder, "### %s\n\n", singleLine(description))
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
		if strings.TrimSpace(step.impact) != "" {
			fmt.Fprintf(&builder, impactLabel, step.impact)
		}
		if language == "pt-br" {
			fmt.Fprintf(&builder, "### Passo %d: %s\n", index+1, action)
		} else {
			fmt.Fprintf(&builder, "### Step %d: %s\n", index+1, action)
		}
		builder.WriteString("```bash\n")
		builder.WriteString(step.command)
		builder.WriteString("\n```\n")
		if strings.TrimSpace(step.output) != "" {
			writeOutputBlock(&builder, step.output)
		}
		if strings.TrimSpace(step.impact) == "" {
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
