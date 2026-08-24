package cmd

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/config"
	"github.com/spf13/cobra"
)

func newAuthCommand() *cobra.Command {
	auth := &cobra.Command{
		Use:   "auth",
		Short: "Inspects the credential stored for the current operating-system user",
	}
	status := &cobra.Command{
		Use:   "status",
		Short: "Validates the stored token and displays the Hub identity",
		Args:  cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			identity, err := client.Me(command.Context())
			if err != nil {
				return err
			}
			if err := validateExpectedIdentity(identity.Username); err != nil {
				return err
			}
			// Mostra todas as areas autorizadas: com mais de uma, saber apenas
			// a primaria nao diz o que `lucien start -r` vai aceitar.
			fmt.Fprintf(
				command.OutOrStdout(),
				"Authenticated as %s (%s); level=%s areas=%s.\n",
				identity.Username,
				identity.ID,
				identity.RoleLevel,
				strings.Join(identity.Areas(), ", "),
			)
			return nil
		},
	}
	ensure := &cobra.Command{
		Use:   "ensure",
		Short: "Silently validates the stored identity or prompts for a token",
		Args:  cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			host, err := configuredAPIHost()
			if err != nil {
				return err
			}
			_, token, err := config.LoadAuthenticatedProfile(host)
			if err != nil {
				return authenticateAndSave(command)
			}
			client, err := clientWithToken(token)
			if err != nil {
				return err
			}
			identity, err := client.Me(command.Context())
			if err != nil {
				if api.IsHTTPStatus(err, http.StatusUnauthorized) {
					fmt.Fprintln(command.ErrOrStderr(), "Stored credential is no longer valid; enter a new token.")
					return authenticateAndSave(command)
				}
				return err
			}
			return validateExpectedIdentity(identity.Username)
		},
	}
	auth.AddCommand(status, ensure)
	return auth
}
