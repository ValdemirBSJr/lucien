package cmd

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"strconv"

	"github.com/AlecAivazis/survey/v2"
	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/draft"
	"github.com/lucien-runbook/lucien/internal/editor"
	"github.com/lucien-runbook/lucien/internal/recording"
	"github.com/lucien-runbook/lucien/internal/runbookdraft"
	"github.com/spf13/cobra"
)

type activeJobLister interface {
	Active(context.Context) ([]api.Job, error)
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
				steps := runbookdraft.SelectedCommandSteps(
					job.Commands,
					job.CommandOutputs,
					job.RunbookSuggestions.CommandImpacts,
					selected,
				)
				partida, err = runbookdraft.MarkdownTemplate(
					runbookdraft.DisplayName(job.Name),
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
		newJobCatCommand(),
		newJobSentCommand(),
		newJobDeleteCommand(),
		newJobStatusCommand(),
		newJobRetryCommand(),
	)
	return jobCommand
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
				command.Context(), job.ID, string(content), hex.EncodeToString(idempotency[:]), nil,
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

// uuidCompleto reconhece o identificador que o rascunho usa como chave.
var uuidCompleto = regexp.MustCompile(
	`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`,
)

func newJobCatCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "cat <job_id>",
		Short: "Prints the saved draft without opening the editor",
		Long: "Prints the saved draft for a job.\n\n" +
			"Reads the local draft and never contacts the Hub. A draft refused " +
			"at publish time never reached the Hub, and it is exactly the one " +
			"worth reading -- a diagnostic command must keep working when the " +
			"Hub does not.\n\n" +
			"That is why it takes the exact job ID and not a review index or a " +
			"name: resolving those requires the Hub's list. Output goes to " +
			"stdout, so it can be piped.",
		Args: cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			// O rascunho vai inteiro para o terminal. Dentro de uma captura
			// isso entraria no proprio log -- e ele pode conter justamente o
			// segredo que fez a publicacao ser recusada.
			if recording.InsideRecordedSession() {
				return errors.New(
					"refusing to print a draft inside a recorded session; " +
						"run lucien job cat from another terminal",
				)
			}

			// Somente o ID exato. Indice e nome exigiriam a lista do Hub, e
			// um comando de diagnostico que depende do Hub falha justamente
			// quando ha o que diagnosticar. `lucien runbook revise` cobra o
			// UUID pelo mesmo tipo de razao: precisao acima de conveniencia.
			identifier := args[0]
			if !uuidCompleto.MatchString(identifier) {
				return errors.New(
					"job cat takes the exact job ID, not a review index or a " +
						"name; it reads the local draft and never queries the Hub",
				)
			}
			content, err := draft.Load(identifier)
			if err != nil {
				return err
			}
			_, err = command.OutOrStdout().Write(content)
			return err
		},
	}
}
