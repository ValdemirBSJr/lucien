package cmd

import (
	"fmt"
	"text/tabwriter"

	"github.com/spf13/cobra"
)

func newReviewsCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "reviews",
		Short: "Lists the active user's jobs awaiting completion",
		Args:  cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			jobs, err := client.Active(command.Context())
			if err != nil {
				return err
			}
			writer := tabwriter.NewWriter(command.OutOrStdout(), 0, 4, 2, ' ', 0)
			fmt.Fprintln(writer, "ID\tNAME\tSTATUS\tCREATED AT")
			for _, job := range jobs {
				fmt.Fprintf(writer, "%s\t%s\t%s\t%s\n", job.ID, job.Name, job.Status, job.CreatedAt.Local().Format("2006-01-02 15:04"))
			}
			return writer.Flush()
		},
	}
}
