package cmd

import (
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"

	"github.com/spf13/cobra"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/editor"
	"github.com/lucien-runbook/lucien/internal/recording"
)

// Revisar uma publicação imutável é operação de consequência: exigimos o UUID
// canônico, sem índice da lista de reviews e sem resolução por nome. O operador
// precisa saber exatamente qual versão está corrigindo.
var canonicalRunbookID = regexp.MustCompile(
	`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`,
)

func newRunbookCommand() *cobra.Command {
	runbookCommand := &cobra.Command{
		Use:   "runbook",
		Short: "Operations on already published runbooks",
	}
	runbookCommand.AddCommand(newRunbookReviseCommand())
	runbookCommand.AddCommand(newRunbookCatCommand())
	return runbookCommand
}

func newRunbookCatCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "cat <published_runbook_uuid>",
		Short: "Prints a published runbook without opening the editor",
		Long: "Prints the body of a published runbook.\n\n" +
			"Same relation to `revise` that `job cat` has to `job`: read what " +
			"is there without the risk of editing it. Consulting a procedure " +
			"should not put the operator in front of an editor with an " +
			"immutable publication open.\n\n" +
			"Unlike `job cat`, this one does query the Hub: a published runbook " +
			"exists only there, and it already passed the secret policy and the " +
			"DLP before being published.\n\n" +
			"Requires the exact UUID, like `revise`. Output goes to stdout, so " +
			"it can be piped.",
		Args: cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			// O runbook inteiro vai para o terminal. Dentro de uma captura ele
			// entraria no log como saida do ultimo comando, e o proximo runbook
			// nasceria com outro embutido dentro. Aqui nao ha risco de segredo
			// -- o conteudo ja foi publicado, logo ja passou pela politica --,
			// e sim de sujar a captura.
			if recording.InsideRecordedSession() {
				return errors.New(
					"refusing to print a runbook inside a recorded session; " +
						"run lucien runbook cat from another terminal",
				)
			}

			runbookID := strings.TrimSpace(args[0])
			if !canonicalRunbookID.MatchString(runbookID) {
				return errors.New(
					"provide the exact published runbook UUID; " +
						"runbook cat accepts neither review index nor name",
				)
			}

			client, _, err := activeClient()
			if err != nil {
				return err
			}

			published, err := client.PublishedContent(command.Context(), runbookID)
			if err != nil {
				return err
			}

			out := command.OutOrStdout()
			if _, err := io.WriteString(out, published.Markdown); err != nil {
				return err
			}
			// Uma quebra so quando falta: duas atrapalhariam quem redireciona a
			// saida para arquivo e depois compara com o publicado.
			if !strings.HasSuffix(published.Markdown, "\n") {
				if _, err := io.WriteString(out, "\n"); err != nil {
					return err
				}
			}
			return nil
		},
	}
}

func newRunbookReviseCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "revise <published_runbook_uuid>",
		Short: "Revises a published runbook, creating an immutable successor",
		Long: "Downloads the published body, opens it in $EDITOR and sends the " +
			"result back to the Hub, which creates a new immutable version and " +
			"preserves the lineage. Requires the exact UUID: neither the reviews " +
			"index nor the runbook name is accepted.",
		Args: cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			runbookID := strings.TrimSpace(args[0])
			if !canonicalRunbookID.MatchString(runbookID) {
				return errors.New(
					"provide the exact published runbook UUID; " +
						"revise accepts neither review index nor name",
				)
			}

			client, _, err := activeClient()
			if err != nil {
				return err
			}

			published, err := client.PublishedContent(command.Context(), runbookID)
			if err != nil {
				return err
			}

			edited, err := editor.Edit([]byte(published.Markdown))
			if err != nil {
				return err
			}
			// Sem alteração não há sucessor a criar: publicar uma cópia idêntica
			// só polui a linhagem e consome outro UUID.
			if strings.TrimSpace(string(edited)) == strings.TrimSpace(published.Markdown) {
				fmt.Fprintln(
					command.OutOrStdout(),
					"No changes detected; revision cancelled.",
				)
				return nil
			}

			key, err := api.NewIdempotencyKey()
			if err != nil {
				return err
			}
			revision, err := client.ReviseRunbook(
				command.Context(),
				runbookID,
				string(edited),
				published.ContentHash,
				key,
				nil,
			)
			if err != nil {
				return err
			}

			fmt.Fprintf(
				command.OutOrStdout(),
				"Revision published.\nNew Job_ID: %s\nSupersedes: %s\n",
				revision.ID,
				runbookID,
			)
			return nil
		},
	}
}
