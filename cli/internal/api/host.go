package api

import (
	"errors"
	"fmt"
	"net/url"
	"strings"
)

// ValidateAPIHost aceita apenas a origem do Hub, sem nada pendurado nela.
//
// O valor vem do ambiente e é usado em dois lugares que perdoam pouco: ele
// prefixa toda chamada e compõe o nome da conta no keyring. Um `userinfo`
// gravaria credencial no nome da entrada e vazaria em qualquer lugar que
// registre a URL; um caminho ou query deslocaria silenciosamente cada
// endpoint, e o erro apareceria como 404 do Hub em vez de configuração
// inválida.
//
// Aceitar `/` no fim é conveniência: `https://hub:8443` e `https://hub:8443/`
// descrevem a mesma origem, e recusar a barra só produziria atrito.
func ValidateAPIHost(apiHost string) (*url.URL, error) {
	if strings.TrimSpace(apiHost) != apiHost {
		return nil, errors.New("API_HOST must not have leading or trailing spaces")
	}
	parsed, err := url.Parse(apiHost)
	if err != nil {
		return nil, fmt.Errorf("API_HOST is not a valid URL: %w", err)
	}
	if parsed.Scheme != "https" {
		return nil, errors.New("API_HOST must use https")
	}
	if parsed.Opaque != "" {
		return nil, errors.New("API_HOST must be an absolute URL with //host")
	}
	if parsed.Host == "" {
		return nil, errors.New("API_HOST must include a host")
	}
	if parsed.User != nil {
		return nil, errors.New(
			"API_HOST must not carry credentials; they belong in the keyring",
		)
	}
	if parsed.RawQuery != "" || parsed.ForceQuery {
		return nil, errors.New("API_HOST must not carry a query string")
	}
	if parsed.Fragment != "" || parsed.RawFragment != "" {
		return nil, errors.New("API_HOST must not carry a fragment")
	}
	if parsed.Path != "" && parsed.Path != "/" {
		return nil, fmt.Errorf(
			"API_HOST must be an origin without a path; got %q", parsed.Path,
		)
	}
	// Normaliza para a origem, sem a barra final: `JoinPath` produz caminho
	// duplo quando a base ja termina em barra.
	origem := &url.URL{Scheme: parsed.Scheme, Host: parsed.Host}
	return origem, nil
}
