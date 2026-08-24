package cmd

import (
	"errors"
	"fmt"
	"os"

	"github.com/lucien-runbook/lucien/internal/config"
	"github.com/spf13/cobra"
)

func newCreateCommand() *cobra.Command {
	create := &cobra.Command{Use: "create", Short: "Creates local or remote resources"}
	create.AddCommand(&cobra.Command{
		Use:   "user <name>",
		Short: "Creates the first administrator through bootstrap and activates the local profile",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			bootstrap := os.Getenv("LUCIEN_BOOTSTRAP_KEY")
			if bootstrap == "" {
				return errors.New("LUCIEN_BOOTSTRAP_KEY is not configured")
			}
			client, err := clientWithToken(bootstrap)
			if err != nil {
				return err
			}
			created, err := client.BootstrapAdmin(command.Context(), args[0])
			if err != nil {
				return err
			}
			fmt.Fprintf(
				command.OutOrStdout(),
				"Administrator %s created.\n"+
					"Permanent API token (shown once):\n%s\n",
				created.Username,
				created.APIToken,
			)
			host, err := configuredAPIHost()
			if err != nil {
				return err
			}
			if err := config.SaveAuthenticatedProfile(config.Profile{
				UserID:   created.ID,
				Username: created.Username,
			}, host, created.APIToken); err != nil {
				return fmt.Errorf(
					"administrator created and the token shown above remains valid, but it was not saved locally: %w",
					err,
				)
			}
			fmt.Fprintf(
				command.OutOrStdout(),
				"The token was also stored for the current operating-system user.\n"+
					"Do not run 'lucien login'; validate it with 'lucien auth status'.\n",
			)
			return nil
		},
	})
	return create
}
