package cmd

import (
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/spf13/cobra"
)

var validRoles = map[string]struct{}{
	"junior": {},
	"pleno":  {},
	"senior": {},
	"admin":  {},
}

func newAdminCommand() *cobra.Command {
	admin := &cobra.Command{
		Use:   "admin",
		Short: "Manages Hub identities; authorization is always enforced by the Hub",
	}
	user := &cobra.Command{Use: "user", Short: "Manages user credentials"}
	user.AddCommand(
		newAdminCreateUserCommand(),
		newAdminRotateTokenCommand(),
		newAdminUpdateUserCommand(),
		newAdminRevokeUserCommand(),
		newAdminReinstateUserCommand(),
	)
	admin.AddCommand(user)
	return admin
}

func newAdminCreateUserCommand() *cobra.Command {
	var permissionLevel string
	var roles string
	command := &cobra.Command{
		Use:   "create <name>",
		Short: "Creates a user and displays a four-hour provisional token once",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			if _, valid := validRoles[permissionLevel]; !valid {
				return errors.New("--level must be junior, pleno, senior, or admin")
			}
			primary, extra, err := parseRoles(roles)
			if err != nil {
				return err
			}
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			issued, err := client.CreateUser(
				command.Context(), args[0], permissionLevel, primary, extra,
			)
			if err != nil {
				return err
			}
			printProvisionedCredential(command, issued)
			return nil
		},
	}
	command.Flags().StringVar(
		&permissionLevel, "level", "", "permission level: junior, pleno, senior, or admin",
	)
	command.Flags().StringVarP(
		&roles, "role", "r", "",
		"comma-separated areas; the first is the primary (default without -r on start)",
	)
	return command
}

// parseRoles separa a lista `-r`. A primeira area e a primaria: e ela que
// `lucien start` usa quando roda sem `-r`, e a que aparece no frontmatter.
// As demais sao acessos adicionais.
//
// Atencao ao vocabulario: aqui "role" e AREA, igual ao `-r` do start. O nivel
// de permissao passou a ser `--level`, que antes se chamava `--role`.
func parseRoles(value string) (string, []string, error) {
	visto := map[string]bool{}
	var areas []string
	for _, bruto := range strings.Split(value, ",") {
		area := strings.TrimSpace(bruto)
		if area == "" {
			continue
		}
		if !domainFunctionGrammar.MatchString(area) {
			return "", nil, fmt.Errorf(
				"area %q must use 3-64 lowercase characters starting with a letter",
				area,
			)
		}
		if !visto[area] {
			visto[area] = true
			areas = append(areas, area)
		}
	}
	if len(areas) == 0 {
		return "", nil, errors.New("-r requires at least one area")
	}
	return areas[0], areas[1:], nil
}

func newAdminRotateTokenCommand() *cobra.Command {
	var scope string
	command := &cobra.Command{
		Use:     "issue-provisional-token <user-id-or-name>",
		Aliases: []string{"rotate-token"},
		Short:   "Invalidates the permanent token and issues a four-hour provisional token",
		Args:    cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			issued, err := client.IssueProvisionalToken(command.Context(), args[0], scope)
			if err != nil {
				return err
			}
			printProvisionedCredential(command, issued)
			return nil
		},
	}
	command.Flags().StringVar(
		&scope, "scope", "",
		"Isolates the issued credential to a named scope (e.g. \"personal\"), "+
			"leaving other scopes (like the jump server's) untouched. "+
			"Omit for the default, unscoped credential.",
	)
	return command
}

func newAdminUpdateUserCommand() *cobra.Command {
	var permissionLevel string
	var roles string
	command := &cobra.Command{
		Use:   "update <user-id-or-name>",
		Short: "Updates a user's permission level or areas",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			if permissionLevel == "" && roles == "" {
				return errors.New("provide --level, -r, or both")
			}
			if permissionLevel != "" {
				if _, valid := validRoles[permissionLevel]; !valid {
					return errors.New("--level must be junior, pleno, senior, or admin")
				}
			}
			var primary string
			var extra []string
			if roles != "" {
				var err error
				if primary, extra, err = parseRoles(roles); err != nil {
					return err
				}
			}
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			// `-r` ausente preserva as areas atuais; presente, substitui o
			// conjunto inteiro. Revogar uma area e omiti-la da lista.
			updated, err := client.UpdateUser(
				command.Context(), args[0], permissionLevel, primary, extra, roles != "",
			)
			if err != nil {
				return err
			}
			areas := updated.DomainFunction
			if len(updated.ExtraDomains) > 0 {
				areas += ", " + strings.Join(updated.ExtraDomains, ", ")
			}
			fmt.Fprintf(
				command.OutOrStdout(),
				"User %s updated; level=%s areas=%s.\n",
				updated.Username,
				updated.RoleLevel,
				areas,
			)
			return nil
		},
	}
	command.Flags().StringVar(&permissionLevel, "level", "", "new permission level")
	command.Flags().StringVarP(
		&roles, "role", "r", "",
		"comma-separated areas, replacing the current set; the first is the primary",
	)
	return command
}

func newAdminRevokeUserCommand() *cobra.Command {
	var confirmed bool
	command := &cobra.Command{
		Use:   "revoke <user-id-or-name>",
		Short: "Revokes a user's token immediately",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			if !confirmed {
				return errors.New(
					"revocation kills every credential of the user, in every scope; " +
						"the way back is 'reinstate', which issues a new one. " +
						"Repeat with --yes",
				)
			}
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			if err := client.RevokeUser(command.Context(), args[0]); err != nil {
				return err
			}
			fmt.Fprintf(command.OutOrStdout(), "User %s revoked.\n", args[0])
			return nil
		},
	}
	command.Flags().BoolVar(
		&confirmed, "yes", false, "Confirm the revocation of every credential",
	)
	return command
}

// A readmissao existe porque desligar alguem e revogar sao a mesma acao, mas
// voltar de uma licenca nao e criar um usuario novo: o `create` recusaria o
// username existente, e o `issue-provisional-token` recusa quem esta inativo.
// Sem este comando o caminho de volta era UPDATE no banco, fora de qualquer
// trilha de auditoria.
func newAdminReinstateUserCommand() *cobra.Command {
	var confirmed bool
	command := &cobra.Command{
		Use:   "reinstate <user-id-or-name>",
		Short: "Reactivates a revoked user and issues a four-hour provisional token",
		Args:  cobra.ExactArgs(1),
		RunE: func(command *cobra.Command, args []string) error {
			if !confirmed {
				return errors.New(
					"reinstating restores access to someone who was revoked; " +
						"repeat with --yes",
				)
			}
			client, _, err := activeClient()
			if err != nil {
				return err
			}
			issued, err := client.ReinstateUser(command.Context(), args[0])
			if err != nil {
				return err
			}
			printProvisionedCredential(command, issued)
			fmt.Fprintf(
				command.OutOrStdout(),
				"The credentials revoked earlier stay invalid; this is a new one.\n",
			)
			return nil
		},
	}
	command.Flags().BoolVar(
		&confirmed, "yes", false, "Confirm the reinstatement",
	)
	return command
}

func printProvisionedCredential(command *cobra.Command, issued api.ProvisionedUser) {
	// O token provisório deve ser transferido por um cofre ou canal aprovado e
	// nunca executado dentro de uma sessão que esteja sendo gravada pelo Lucien.
	fmt.Fprintf(
		command.OutOrStdout(),
		"User %s created or updated (id=%s, role=%s, domain=%s).\n"+
			"Provisional API token (single use, expires at %s):\n%s\n"+
			"On that user's machine, run 'lucien login' within four hours.\n",
		issued.Username,
		issued.ID,
		issued.RoleLevel,
		issued.DomainFunction,
		issued.ExpiresAt.Local().Format(time.RFC3339),
		issued.ProvisionalToken,
	)
}
