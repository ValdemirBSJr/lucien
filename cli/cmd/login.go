package cmd

import (
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/config"
	"github.com/spf13/cobra"
	"golang.org/x/term"
)

func newLoginCommand() *cobra.Command {
	var tokenStdin bool
	var quiet bool
	command := &cobra.Command{
		Use:   "login",
		Short: "Stores a newly issued Hub token for the current operating-system user",
		Args:  cobra.NoArgs,
		RunE: func(command *cobra.Command, _ []string) error {
			return authenticateAndSaveWithOptions(command, tokenStdin, quiet)
		},
	}
	command.Flags().BoolVar(
		&tokenStdin,
		"token-stdin",
		false,
		"reads the token from standard input without exposing it in process arguments",
	)
	command.Flags().BoolVar(
		&quiet,
		"quiet",
		false,
		"suppresses successful login output for controlled automation",
	)
	return command
}

func authenticateAndSave(command *cobra.Command) error {
	return authenticateAndSaveWithOptions(command, false, false)
}

func authenticateAndSaveWithOptions(
	command *cobra.Command, tokenStdin bool, quiet bool,
) error {
	token, err := loginToken(command, tokenStdin)
	if err != nil {
		return err
	}
	client, err := clientWithToken(token)
	if err != nil {
		return err
	}
	var identity api.UserIdentity
	issuedPermanent := false
	if strings.HasPrefix(token, "luc_tmp_") {
		issued, exchangeErr := client.ExchangeProvisionalToken(command.Context())
		if exchangeErr != nil {
			return exchangeErr
		}
		token = issued.APIToken
		identity = api.UserIdentity{
			ID:             issued.ID,
			Username:       issued.Username,
			RoleLevel:      issued.RoleLevel,
			DomainFunction: issued.DomainFunction,
			IsActive:       issued.IsActive,
		}
		issuedPermanent = true
	} else {
		identity, err = client.Me(command.Context())
		if err != nil {
			return err
		}
	}
	if err := validateExpectedIdentity(identity.Username); err != nil {
		return err
	}
	host, err := configuredAPIHost()
	if err != nil {
		return err
	}
	if err := config.SaveAuthenticatedProfile(config.Profile{
		UserID:   identity.ID,
		Username: identity.Username,
	}, host, token); err != nil {
		if issuedPermanent {
			if quiet {
				return fmt.Errorf(
					"permanent token was issued but could not be stored; request a new jump enrollment: %w",
					err,
				)
			}
			return fmt.Errorf(
				"permanent token was issued but could not be stored: %w; token: %s",
				err,
				token,
			)
		}
		return err
	}
	if quiet {
		return nil
	}
	if issuedPermanent {
		fmt.Fprintf(
			command.OutOrStdout(),
			"Permanent API token (shown once):\n%s\n",
			token,
		)
	}
	fmt.Fprintf(
		command.OutOrStdout(),
		"Login completed for %s; privileges are always resolved by the Hub.\n",
		identity.Username,
	)
	return nil
}

func loginToken(command *cobra.Command, tokenStdin bool) (string, error) {
	if tokenStdin {
		if input, ok := command.InOrStdin().(*os.File); ok && term.IsTerminal(int(input.Fd())) {
			return "", fmt.Errorf("--token-stdin requires redirected standard input")
		}
		value, err := io.ReadAll(io.LimitReader(command.InOrStdin(), 4097))
		if err != nil {
			return "", fmt.Errorf("read token from standard input: %w", err)
		}
		if len(value) > 4096 {
			return "", fmt.Errorf("token exceeds 4096 bytes")
		}
		token := strings.TrimSpace(string(value))
		if token == "" {
			return "", fmt.Errorf("token cannot be empty")
		}
		return token, nil
	}
	fmt.Fprint(command.ErrOrStderr(), "API token (provisional or permanent): ")
	value, err := term.ReadPassword(int(os.Stdin.Fd()))
	fmt.Fprintln(command.ErrOrStderr())
	if err != nil {
		return "", fmt.Errorf("read token without echo: %w", err)
	}
	token := strings.TrimSpace(string(value))
	if token == "" {
		return "", fmt.Errorf("token cannot be empty")
	}
	return token, nil
}
