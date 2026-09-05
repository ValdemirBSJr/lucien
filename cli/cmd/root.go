package cmd

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/config"
	"github.com/spf13/cobra"
)

// version e gravada no binario pelo scripts/build-cli.sh via -ldflags. Um
// build local sem essa injecao mostra `dev`, o que ja distingue binario de
// desenvolvimento de pacote publicado -- saber qual versao esta na maquina
// e a primeira pergunta quando um comportamento diverge do esperado.
var version = "dev"

// Version expoe a versao para quem monta a saida de ajuda e diagnostico.
func Version() string { return version }

func NewRootCommand() *cobra.Command {
	root := &cobra.Command{
		Use:               "lucien",
		Version:           version,
		Short:             "Records sessions and publishes reviewed playbooks",
		SilenceUsage:      true,
		SilenceErrors:     true,
		CompletionOptions: cobra.CompletionOptions{HiddenDefaultCmd: true},
		PersistentPreRunE: func(command *cobra.Command, _ []string) error {
			if !shouldEnforceJumpAuthentication(command) {
				return nil
			}
			client, _, err := activeClient()
			if err != nil {
				return fmt.Errorf("Lucien authentication is required on this jump server: %w", err)
			}
			identity, err := client.Me(command.Context())
			if err != nil {
				return fmt.Errorf("Lucien authentication is required on this jump server: %w", err)
			}
			return validateExpectedIdentity(identity.Username)
		},
	}
	root.AddCommand(
		newAuthCommand(),
		newAdminCommand(),
		newLoginCommand(),
		newCreateCommand(),
		newReviewsCommand(),
		newJobCommand(),
		newRunbookCommand(),
		newSessionCommand(),
		newStartCommand(),
		newStopCommand(),
		newUploadCommand(),
	)
	return root
}

func isJumpProtected(command *cobra.Command) bool {
	parts := strings.Fields(command.CommandPath())
	if len(parts) < 2 {
		return false
	}
	switch parts[1] {
	case "start", "upload", "reviews", "job", "admin", "create":
		return true
	default:
		return false
	}
}

func shouldEnforceJumpAuthentication(command *cobra.Command) bool {
	return os.Getenv("LUCIEN_JUMP_MODE") == "true" && isJumpProtected(command)
}

func validateExpectedIdentity(username string) error {
	expected := strings.TrimSpace(os.Getenv("LUCIEN_EXPECTED_USERNAME"))
	if expected != "" && username != expected {
		return fmt.Errorf(
			"stored Hub identity %q does not match the expected jump-server identity %q",
			username,
			expected,
		)
	}
	return nil
}

func activeClient() (*api.Client, config.Profile, error) {
	host, err := configuredAPIHost()
	if err != nil {
		return nil, config.Profile{}, err
	}
	profile, token, err := config.LoadAuthenticatedProfile(host)
	if err != nil {
		return nil, config.Profile{}, err
	}
	client, err := clientWithToken(token)
	return client, profile, err
}

func clientWithToken(token string) (*api.Client, error) {
	host, err := configuredAPIHost()
	if err != nil {
		return nil, err
	}
	caFile := os.Getenv("TLS_CA_FILE")
	if caFile == "" {
		return nil, errors.New("TLS_CA_FILE is not configured")
	}
	return api.NewClient(host, token, caFile)
}

func configuredAPIHost() (string, error) {
	host := os.Getenv("API_HOST")
	if host == "" {
		return "", errors.New("API_HOST is not configured")
	}
	return host, nil
}
