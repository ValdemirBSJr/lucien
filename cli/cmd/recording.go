package cmd

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/AlecAivazis/survey/v2"
	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/recording"
	"github.com/spf13/cobra"
)

type sessionUploader interface {
	Upload(context.Context, string, string, string, bool, string) (api.Job, error)
	GetJob(context.Context, string) (api.Job, error)
}

// `-r` recebe a AREA (acessos, servidores, roteamento), que o backend chama de
// domain_function -- nao o nivel de permissao, que e RoleLevel. A palavra "role"
// carrega os dois sentidos no projeto; aqui e sempre o primeiro.
//
// A lista valida vive no Hub (RUNBOOK_DOMAIN_FUNCTIONS); `start` grava offline,
// entao aqui so a gramatica. Quem recusa um nome que nao existe e o upload.
var domainFunctionGrammar = regexp.MustCompile(`^[a-z][a-z0-9_]{2,63}$`)

// clearPendingSession resolve a sessao que ja esta em disco antes de gravar
// por cima dela.
//
// Antes disto o `start` sobrescrevia o session.json calado e criava um log
// novo. O log anterior ficava orfao: nada mais apontava para ele, e nem
// `upload` nem `stop` o alcancavam. Quando a sessao anterior tinha sido
// recusada pela politica de segredo, era o segredo que ficava para tras.
func clearPendingSession(command *cobra.Command, assumeYes bool) error {
	session, existe, err := recording.Pending()
	if err != nil {
		return err
	}
	if !existe {
		return nil
	}
	// Uma captura viva nao e um orfao, e perguntar seria a pergunta errada:
	// sobrescrever deixaria o PTY rodando sem nada que o descrevesse.
	if session.Status == "RUNNING" {
		return fmt.Errorf(
			"session %s is still recording; run lucien stop before starting another",
			session.JobName,
		)
	}
	if !assumeYes {
		fmt.Fprintf(
			command.ErrOrStderr(),
			"Session %s is stopped and was never uploaded.\n"+
				"Starting a new recording deletes it and its log.\n\n"+
				"  lucien session cat      read it\n"+
				"  lucien session edit     fix it and upload again\n"+
				"  lucien session discard  throw it away\n\n",
			session.JobName,
		)
		confirmed := false
		if err := survey.AskOne(&survey.Confirm{
			Message: "Discard it and start a new recording?",
			Default: false,
		}, &confirmed); err != nil {
			return err
		}
		if !confirmed {
			return fmt.Errorf(
				"aborted; session %s was kept -- read it with lucien session cat, "+
					"fix it with lucien session edit, or remove it with "+
					"lucien session discard",
				session.JobName,
			)
		}
	}
	// Apagar aqui, e nao deixar para o `start` sobrescrever, e o que remove o
	// log antigo: o session.json seria substituido de qualquer forma, mas o
	// arquivo de log so sai por Cleanup.
	if _, _, err := recording.Discard(); err != nil {
		return err
	}
	return nil
}

func newStartCommand() *cobra.Command {
	var description string
	var domainFunction string
	var assumeYes bool
	command := &cobra.Command{
		Use:   "start <provider_name>",
		Short: "Opens a PTY and records stdin/stdout locally",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			description = strings.TrimSpace(description)
			if utf8.RuneCountInString(description) > 280 {
				return errors.New("describe must be at most 280 characters")
			}
			domainFunction = strings.TrimSpace(domainFunction)
			if domainFunction != "" && !domainFunctionGrammar.MatchString(domainFunction) {
				return errors.New(
					"area must use 3-64 lowercase characters starting with a letter",
				)
			}
			if description == "" {
				// A ausência não bloqueia a captura, mas reduz o contexto disponível para a SLM.
				fmt.Fprintln(
					command.ErrOrStderr(),
					"Recommendation: use -d \"short description\" to improve SLM accuracy.",
				)
			}
			if err := clearPendingSession(command, assumeYes); err != nil {
				return err
			}
			session, err := recording.Start(args[0], description, domainFunction)
			if err != nil {
				return err
			}
			// Quem imprime e o processo dono do terminal real. `lucien stop`
			// roda dentro do PTY gravado e mata este processo -- que e
			// justamente quem copia a saida do PTY para a tela -- entao a
			// mensagem dele corria contra a morte do proprio leitor. Aqui o
			// terminal ja foi restaurado e a saida chega sempre.
			printSessionStopped(command, session)
			return nil
		},
	}
	command.Flags().StringVarP(
		&description,
		"describe",
		"d",
		"",
		"short task description (recommended to improve SLM accuracy)",
	)
	command.Flags().StringVarP(
		&domainFunction,
		"role",
		"r",
		"",
		"area to publish under (not a permission level); defaults to your own",
	)
	command.Flags().BoolVarP(
		&assumeYes,
		"yes",
		"y",
		false,
		"discard a stopped, never-uploaded session without asking",
	)
	return command
}

func newStopCommand() *cobra.Command {
	return buildStopCommand(recording.Stop)
}

func buildStopCommand(stop func() (recording.Session, bool, error)) *cobra.Command {
	return &cobra.Command{
		Use:   "stop",
		Short: "Stops the PTY and preserves the session locally",
		Args:  cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			session, dentroDaSessao, err := stop()
			if err != nil {
				return err
			}
			// Dentro do PTY gravado quem imprime e o `start`, no mesmo
			// terminal. Imprimir aqui tambem sairia em duplicata.
			if !dentroDaSessao {
				printSessionStopped(command, session)
			}
			return nil
		},
	}
}

// printSessionStopped e o unico lugar que produz essa mensagem, usado tanto
// pelo `start` ao encerrar quanto pelo `stop` executado de outro terminal.
func printSessionStopped(command *cobra.Command, session recording.Session) {
	if session.LogTruncated {
		fmt.Fprintln(
			command.ErrOrStderr(),
			"Warning: the local log reached its limit and was truncated; commands at the end may be missing.",
		)
	}
	fmt.Fprintf(
		command.OutOrStdout(),
		"Session %s stopped and preserved locally.\n"+
			"Next: lucien upload\n"+
			"After acceptance, upload will return the Job_ID and status command.\n",
		session.JobName,
	)
}

func newUploadCommand() *cobra.Command {
	return buildUploadCommand(
		recording.PendingUpload,
		func() (sessionUploader, error) {
			client, _, err := activeClient()
			return client, err
		},
		recording.Cleanup,
	)
}

func buildUploadCommand(
	pending func() (recording.Session, string, error),
	clientFactory func() (sessionUploader, error),
	cleanup func(recording.Session) error,
) *cobra.Command {
	var skipEnrichment bool
	uploadCommand := &cobra.Command{
		Use:   "upload",
		Short: "Uploads a stopped local session to the Hub",
		Args:  cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			session, sanitizedLog, err := pending()
			if err != nil {
				return err
			}
			if session.LogTruncated {
				fmt.Fprintln(
					command.ErrOrStderr(),
					"Warning: this session's log was truncated at the local limit; extraction may be incomplete.",
				)
			}
			client, err := clientFactory()
			if err != nil {
				return fmt.Errorf("session preserved locally: %w", err)
			}
			job, uploadErr := client.Upload(
				command.Context(),
				session.JobName,
				sanitizedLog,
				session.Description,
				skipEnrichment,
				session.DomainFunction,
			)
			if uploadErr != nil {
				// Reconcilia timeout após o Hub criar o Job usando o nome aleatório da sessão.
				job, err = client.GetJob(command.Context(), session.JobName)
				if err != nil {
					return fmt.Errorf("upload failed; session preserved locally: %w", uploadErr)
				}
			}
			if err := cleanup(session); err != nil {
				return fmt.Errorf("job %s accepted, but local files were not removed: %w", job.ID, err)
			}
			fmt.Fprintf(
				command.OutOrStdout(),
				"Job_ID: %s\nStatus: %s. Check progress with: lucien job status %s\n",
				job.ID,
				job.Status,
				job.ID,
			)
			return nil
		},
	}
	uploadCommand.Flags().BoolVarP(
		&skipEnrichment,
		"skip-enrichment",
		"s",
		false,
		"Skips the SLM enrichment step; the draft keeps only the extracted commands",
	)
	return uploadCommand
}
