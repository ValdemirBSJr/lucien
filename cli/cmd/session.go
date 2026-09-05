package cmd

import (
	"errors"
	"fmt"

	"github.com/AlecAivazis/survey/v2"
	"github.com/lucien-runbook/lucien/internal/recording"
	"github.com/spf13/cobra"
)

func newSessionCommand() *cobra.Command {
	command := &cobra.Command{
		Use:   "session",
		Short: "Inspects or discards the local recorded session",
		Long: "A session stays on disk from `lucien stop` until the Hub accepts " +
			"it. When the upload is refused -- by the secret policy, for " +
			"example -- it stays there indefinitely, because the automatic " +
			"cleanup only runs after an accepted upload. These two commands " +
			"are how you look at it and how you get rid of it.",
	}
	command.AddCommand(newSessionCatCommand(), newSessionDiscardCommand())
	return command
}

func newSessionCatCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "cat",
		Short: "Prints the local session exactly as the upload would send it",
		Long: "Prints the recorded session with the ANSI sequences stripped -- " +
			"byte for byte what `lucien upload` would send to the Hub.\n\n" +
			"It is the recording, not a list of extracted commands. Separating " +
			"command from output is the Hub's job, and its grammar covers both " +
			"POSIX shells and network equipment CLIs. A second grammar here " +
			"would drift from that one, and a filtered view that disagrees " +
			"with what the Hub actually reads is worse than no view: it would " +
			"let you conclude the session is clean when it is not.\n\n" +
			"Output goes to stdout, so it can be piped or grepped.",
		Args: cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			// Mesma razao de `lucien job cat`: o conteudo vai inteiro para o
			// terminal e, dentro de uma captura, entraria no proprio log --
			// justamente o segredo que fez a publicacao ser recusada, que e
			// quando o operador mais tem motivo para ler isto.
			if recording.InsideRecordedSession() {
				return errors.New(
					"refusing to print a session inside a recorded session; " +
						"run lucien session cat from another terminal",
				)
			}
			session, existe, err := recording.Pending()
			if err != nil {
				return err
			}
			if !existe {
				return recording.ErrNoSession
			}
			log, err := recording.Log(session)
			if err != nil {
				return err
			}
			// Cabecalho no stderr para o stdout continuar sendo so a gravacao,
			// que e o que alguem redireciona ou passa por grep.
			fmt.Fprintf(
				command.ErrOrStderr(),
				"Session: %s\nStatus:  %s\n\n",
				session.JobName,
				session.Status,
			)
			fmt.Fprint(command.OutOrStdout(), log)
			return nil
		},
	}
}

func newSessionDiscardCommand() *cobra.Command {
	var assumeYes bool
	command := &cobra.Command{
		Use:   "discard",
		Short: "Permanently deletes the local session and its recorded log",
		Long: "Removes the session state and the log file from this machine. " +
			"Nothing is sent to the Hub -- a refused session never created a " +
			"job there, so there is nothing to delete on the other side.\n\n" +
			"Use it after an upload refused by the secret policy: the refusal " +
			"keeps the secret out of the Hub, but the recording that contains " +
			"it stays on this disk until you remove it.",
		Args: cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			session, existe, err := recording.Pending()
			if err != nil {
				return err
			}
			if !existe {
				return recording.ErrNoSession
			}
			// Apagar o estado de uma captura viva deixaria o PTY rodando sem
			// nada que o descrevesse -- nem `stop` o encontraria depois.
			if session.Status == "RUNNING" {
				return errors.New(
					"session is still running; run lucien stop before discarding it",
				)
			}
			if !assumeYes {
				confirmed := false
				if err := survey.AskOne(&survey.Confirm{
					Message: fmt.Sprintf(
						"Permanently delete local session %s and its recorded log?",
						session.JobName,
					),
					Default: false,
				}, &confirmed); err != nil {
					return err
				}
				if !confirmed {
					return nil
				}
			}
			if _, _, err := recording.Discard(); err != nil {
				return err
			}
			fmt.Fprintf(
				command.OutOrStdout(),
				"Local session %s discarded; the recorded log was removed.\n",
				session.JobName,
			)
			return nil
		},
	}
	command.Flags().BoolVarP(&assumeYes, "yes", "y", false, "skip confirmation")
	return command
}
