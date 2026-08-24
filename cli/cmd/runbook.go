package cmd

import (
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/spf13/cobra"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/editor"
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
	return runbookCommand
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
