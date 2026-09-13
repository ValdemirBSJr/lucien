package cmd

import (
	"errors"
	"fmt"
	"io"
	"regexp"
	"sort"
	"strings"
	"text/tabwriter"
	"time"

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
	runbookCommand.AddCommand(newRunbookListCommand())
	return runbookCommand
}

func newRunbookListCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "Lists the published runbooks in your areas that revise accepts",
		Long: "Lists the published runbooks the current identity reaches through " +
			"its areas -- all of them, the primary and the extra ones; an admin " +
			"sees every area.\n\n" +
			"Only the current version of each runbook is shown. A revision " +
			"creates a new version with its own UUID, and `revise` refuses the " +
			"older one with a conflict, so listing it would only lead there.\n\n" +
			"Read-only: it changes nothing. The ID column is what " +
			"`lucien runbook cat` and `lucien runbook revise` take.",
		Args: cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			summaries, err := client.PublishedRunbooksMine(command.Context())
			if err != nil {
				return err
			}
			return writeRunbookList(
				command.OutOrStdout(), command.ErrOrStderr(), currentRunbooks(summaries),
			)
		},
	}
}

// currentRunbooks deixa so a ponta de cada linhagem -- a versao que o revise
// aceita -- na ordem em que o operador procura: area, depois nome.
func currentRunbooks(all []api.PublishedRunbookSummary) []api.PublishedRunbookSummary {
	current := make([]api.PublishedRunbookSummary, 0, len(all))
	for _, summary := range all {
		if summary.Latest {
			current = append(current, summary)
		}
	}
	sort.SliceStable(current, func(i, j int) bool {
		if current[i].Area != current[j].Area {
			return current[i].Area < current[j].Area
		}
		if current[i].Name != current[j].Name {
			return current[i].Name < current[j].Name
		}
		return current[i].ID < current[j].ID
	})
	return current
}

// writeRunbookList escreve a tabela no stdout e a dica no stderr, para que o
// stdout seja so a tabela para quem a passa adiante.
func writeRunbookList(out, hint io.Writer, runbooks []api.PublishedRunbookSummary) error {
	if len(runbooks) == 0 {
		_, err := fmt.Fprintln(out, "No published runbook in your areas.")
		return err
	}
	writer := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
	fmt.Fprintln(writer, "NAME\tID\tAREA\tPUBLISHED AT")
	for _, runbook := range runbooks {
		fmt.Fprintf(
			writer, "%s\t%s\t%s\t%s\n",
			dashIfEmpty(runbook.Name), runbook.ID, dashIfEmpty(runbook.Area),
			formatPublishedAt(runbook.PublishedAt),
		)
	}
	if err := writer.Flush(); err != nil {
		return err
	}
	_, err := fmt.Fprintln(hint, "\nTo change one: lucien runbook revise <ID>")
	return err
}

// Um Hub anterior ao campo `runbooks` nao manda area nem data: o traco deixa
// claro que falta o dado, em vez de uma coluna vazia que parece erro.
func dashIfEmpty(value string) string {
	if strings.TrimSpace(value) == "" {
		return "—"
	}
	return value
}

func formatPublishedAt(moment time.Time) string {
	if moment.IsZero() {
		return "—"
	}
	return moment.Local().Format("2006-01-02 15:04")
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
