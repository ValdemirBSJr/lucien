package api

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"
)

type Client struct {
	baseURL *url.URL
	token   string
	http    *http.Client
}

type HTTPError struct {
	StatusCode int
	Detail     string
	// RequestID identifica a requisicao nos logs do Hub. Sem ele na mensagem,
	// quem relata uma falha so consegue dizer o horario aproximado, e o Hub
	// gerava o identificador sem que ninguem o visse.
	RequestID string
}

func (e *HTTPError) Error() string {
	if e.RequestID == "" {
		return fmt.Sprintf("Hub rejected the request: %s", e.Detail)
	}
	return fmt.Sprintf(
		"Hub rejected the request: %s (request_id: %s)", e.Detail, e.RequestID,
	)
}

// correlationHeader espelha CABECALHO_CORRELACAO no Hub.
const correlationHeader = "X-Request-Id"

// sanitizeRequestID recusa o que nao couber no formato que o Hub emite.
//
// O valor chega pela rede e vai para o terminal do operador. Um Hub adulterado
// -- ou um proxy no meio -- poderia devolver escape de terminal aqui. O Hub so
// aceita este alfabeto na entrada; exigir o mesmo na saida custa nada.
func sanitizeRequestID(value string) string {
	if len(value) < 8 || len(value) > 64 {
		return ""
	}
	for _, caractere := range value {
		switch {
		case caractere >= 'a' && caractere <= 'z':
		case caractere >= 'A' && caractere <= 'Z':
		case caractere >= '0' && caractere <= '9':
		case caractere == '.' || caractere == '_' || caractere == '-':
		default:
			return ""
		}
	}
	return value
}

func IsHTTPStatus(err error, statusCode int) bool {
	var httpError *HTTPError
	return errors.As(err, &httpError) && httpError.StatusCode == statusCode
}

type Job struct {
	ID                 string             `json:"id"`
	Name               string             `json:"name"`
	Status             string             `json:"status"`
	Description        string             `json:"description"`
	Commands           []string           `json:"commands"`
	CommandOutputs     []string           `json:"command_outputs"`
	RunbookSuggestions RunbookSuggestions `json:"runbook_suggestions"`
	CreatedAt          time.Time          `json:"created_at"`
	StorageURL         string             `json:"storage_url"`
	ProcessingError    string             `json:"processing_error"`
	// SanitizationCount informa quantos dados sensíveis foram neutralizados no Hub.
	SanitizationCount int `json:"sanitization_count,omitempty"`
}

type RunbookSuggestions struct {
	Objective                 string   `json:"objective"`
	ArchitecturePrerequisites []string `json:"architecture_prerequisites"`
	CommandImpacts            []string `json:"command_impacts"`
	RollbackCommands          []string `json:"rollback_commands"`
}

type CreatedUser struct {
	ID             string `json:"id"`
	Username       string `json:"username"`
	RoleLevel      string `json:"role_level"`
	DomainFunction string `json:"domain_function"`
	IsActive       bool   `json:"is_active"`
	APIToken       string `json:"api_token"`
}

type ProvisionedUser struct {
	ID               string    `json:"id"`
	Username         string    `json:"username"`
	RoleLevel        string    `json:"role_level"`
	DomainFunction   string    `json:"domain_function"`
	ExtraDomains     []string  `json:"extra_domains"`
	IsActive         bool      `json:"is_active"`
	ProvisionalToken string    `json:"provisional_token"`
	ExpiresAt        time.Time `json:"expires_at"`
	// So preenchido na primeira vez que esta identidade ganha uma credencial
	// permanente pessoal (fluxo do jump); vazio nas demais chamadas.
	PersonalToken string `json:"personal_token,omitempty"`
}

type UserIdentity struct {
	ID       string `json:"id"`
	Username string `json:"username"`
	// Nivel de permissao (junior/pleno/senior/admin), nao area.
	RoleLevel string `json:"role_level"`
	// Area primaria: o destino quando `lucien start` roda sem `-r`.
	DomainFunction string `json:"domain_function"`
	// Areas adicionais concedidas pelo admin.
	ExtraDomains []string `json:"extra_domains"`
	IsActive     bool     `json:"is_active"`
}

// Areas devolve todas as areas autorizadas, com a primaria primeiro.
func (u UserIdentity) Areas() []string {
	return append([]string{u.DomainFunction}, u.ExtraDomains...)
}

type RunbookConfiguration struct {
	Language string `json:"language"`
	// Areas aceitas em `lucien start -r`, declaradas em RUNBOOK_DOMAIN_FUNCTIONS.
	DomainFunctions []string `json:"domain_functions"`
}

func NewClient(apiHost, token, caFile string) (*Client, error) {
	parsed, err := ValidateAPIHost(apiHost)
	if err != nil {
		return nil, err
	}
	caPEM, err := os.ReadFile(caFile)
	if err != nil {
		return nil, fmt.Errorf("read CA from TLS_CA_FILE: %w", err)
	}
	roots, err := x509.SystemCertPool()
	if err != nil || roots == nil {
		roots = x509.NewCertPool()
	}
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("TLS_CA_FILE does not contain a valid PEM certificate")
	}
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			MinVersion: tls.VersionTLS13,
			RootCAs:    roots,
		},
		MaxIdleConns:        20,
		IdleConnTimeout:     30 * time.Second,
		TLSHandshakeTimeout: 10 * time.Second,
	}
	return &Client{
		baseURL: parsed,
		token:   token,
		http:    &http.Client{Transport: transport, Timeout: 120 * time.Second},
	}, nil
}

// endpoint monta a URL final escapando cada segmento uma única vez: o valor
// escapado sobrevive à serialização via RawPath, sem dupla codificação.
func (c *Client) endpoint(segments ...string) string {
	escaped := make([]string, len(segments))
	for i, segment := range segments {
		escaped[i] = url.PathEscape(segment)
	}
	return c.baseURL.JoinPath(escaped...).String()
}

func (c *Client) BootstrapAdmin(ctx context.Context, username string) (CreatedUser, error) {
	var response CreatedUser
	err := c.doJSON(
		ctx,
		http.MethodPost,
		c.endpoint("bootstrap", "admin"),
		map[string]string{"username": username, "domain_function": "plataforma"},
		&response,
		nil,
	)
	return response, err
}

func (c *Client) Me(ctx context.Context) (UserIdentity, error) {
	var response UserIdentity
	err := c.doJSON(ctx, http.MethodGet, c.endpoint("me"), nil, &response, nil)
	return response, err
}

func (c *Client) RunbookConfiguration(ctx context.Context) (RunbookConfiguration, error) {
	var response RunbookConfiguration
	err := c.doJSON(
		ctx,
		http.MethodGet,
		c.endpoint("configuration", "runbook"),
		nil,
		&response,
		nil,
	)
	return response, err
}

func (c *Client) CreateUser(
	ctx context.Context, username, roleLevel, domainFunction string,
	extraDomains []string,
) (ProvisionedUser, error) {
	var response ProvisionedUser
	if extraDomains == nil {
		// O Hub recusa null; lista vazia e o mesmo que "sem areas adicionais".
		extraDomains = []string{}
	}
	err := c.doJSON(
		ctx,
		http.MethodPost,
		c.endpoint("admin", "users"),
		map[string]any{
			"username":        username,
			"role_level":      roleLevel,
			"domain_function": domainFunction,
			"extra_domains":   extraDomains,
		},
		&response,
		nil,
	)
	return response, err
}

func (c *Client) IssueProvisionalToken(
	ctx context.Context, identifier string, scope string,
) (ProvisionedUser, error) {
	var body any
	if scope != "" {
		body = map[string]string{"scope": scope}
	}
	var response ProvisionedUser
	err := c.doJSON(
		ctx,
		http.MethodPost,
		c.endpoint("admin", "users", identifier, "provisional-token"),
		body,
		&response,
		nil,
	)
	return response, err
}

func (c *Client) ExchangeProvisionalToken(ctx context.Context) (CreatedUser, error) {
	idempotencyKey, err := randomIdempotencyKey()
	if err != nil {
		return CreatedUser{}, err
	}
	for attempt := 0; attempt < 2; attempt++ {
		var response CreatedUser
		err = c.doJSON(
			ctx,
			http.MethodPost,
			c.endpoint("auth", "exchange"),
			map[string]string{},
			&response,
			map[string]string{"Idempotency-Key": idempotencyKey},
		)
		if err == nil {
			return response, nil
		}
		var httpError *HTTPError
		if errors.As(err, &httpError) || ctx.Err() != nil {
			return CreatedUser{}, err
		}
	}
	return CreatedUser{}, err
}

// NewIdempotencyKey expoe o mesmo gerador usado internamente, para que os
// comandos nao inventem outro esquema de chave.
func NewIdempotencyKey() (string, error) {
	return randomIdempotencyKey()
}

func randomIdempotencyKey() (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate idempotency key: %w", err)
	}
	return hex.EncodeToString(value), nil
}

func (c *Client) UpdateUser(
	ctx context.Context, identifier, roleLevel, domainFunction string,
	extraDomains []string, replaceAreas bool,
) (UserIdentity, error) {
	var response UserIdentity
	payload := make(map[string]any, 3)
	if roleLevel != "" {
		payload["role_level"] = roleLevel
	}
	if domainFunction != "" {
		payload["domain_function"] = domainFunction
	}
	// Omitir `extra_domains` preserva as areas atuais; envia-lo substitui o
	// conjunto, e uma lista vazia revoga todas as adicionais.
	if replaceAreas {
		if extraDomains == nil {
			extraDomains = []string{}
		}
		payload["extra_domains"] = extraDomains
	}
	err := c.doJSON(
		ctx,
		http.MethodPatch,
		c.endpoint("admin", "users", identifier),
		payload,
		&response,
		nil,
	)
	return response, err
}

func (c *Client) RevokeUser(ctx context.Context, identifier string) error {
	return c.doJSON(
		ctx,
		http.MethodDelete,
		c.endpoint("admin", "users", identifier),
		nil,
		nil,
		nil,
	)
}

func (c *Client) Upload(
	ctx context.Context, name, rawLog, description string, skipEnrichment bool,
	domainFunction string,
) (Job, error) {
	var response Job
	payload := struct {
		Name           string `json:"name"`
		RawLog         string `json:"raw_log"`
		Description    string `json:"description,omitempty"`
		SkipEnrichment bool   `json:"skip_enrichment"`
		// Omitido quando vazio: ausente significa "o dominio do autor", e o
		// Hub rejeita string vazia como funcao de dominio.
		DomainFunction string `json:"domain_function,omitempty"`
	}{
		Name:           name,
		RawLog:         rawLog,
		Description:    description,
		SkipEnrichment: skipEnrichment,
		DomainFunction: domainFunction,
	}
	err := c.doJSON(ctx, http.MethodPost, c.endpoint("upload"), payload, &response, nil)
	return response, err
}

func (c *Client) Active(ctx context.Context) ([]Job, error) {
	var response []Job
	err := c.doJSON(ctx, http.MethodGet, c.endpoint("jobs", "active"), nil, &response, nil)
	return response, err
}

func (c *Client) GetJob(ctx context.Context, identifier string) (Job, error) {
	var response Job
	err := c.doJSON(ctx, http.MethodGet, c.endpoint("jobs", identifier), nil, &response, nil)
	return response, err
}

func (c *Client) RetryJob(
	ctx context.Context, identifier string, skipEnrichment bool,
) (Job, error) {
	var response Job
	// Sem o flag, o corpo é omitido e o Hub preserva a escolha do upload original.
	var payload any
	if skipEnrichment {
		payload = map[string]bool{"skip_enrichment": true}
	}
	err := c.doJSON(
		ctx,
		http.MethodPost,
		c.endpoint("jobs", identifier, "retry"),
		payload,
		&response,
		nil,
	)
	return response, err
}

// Asset é uma imagem anexada a uma publicação ou revisão, antes do gate de
// segurança do Hub (OCR + gitleaks) -- espelha RunbookAssetInput do backend.
type Asset struct {
	Filename      string `json:"filename"`
	ContentBase64 string `json:"content_base64"`
	MediaType     string `json:"media_type"`
}

func (c *Client) Publish(
	ctx context.Context, identifier, markdown, key string, assets []Asset,
) (Job, error) {
	var response Job
	payload := struct {
		Markdown string  `json:"markdown"`
		Assets   []Asset `json:"assets,omitempty"`
	}{Markdown: markdown, Assets: assets}
	err := c.doJSON(
		ctx,
		http.MethodPost,
		c.endpoint("jobs", identifier, "publish"),
		payload,
		&response,
		map[string]string{"Idempotency-Key": key},
	)
	return response, err
}

func (c *Client) DeleteJob(ctx context.Context, identifier string, force bool) error {
	endpoint := c.endpoint("jobs", identifier)
	if force {
		endpoint += "?force=true"
	}
	return c.doJSON(ctx, http.MethodDelete, endpoint, nil, nil, nil)
}

func (c *Client) doJSON(
	ctx context.Context,
	method, endpoint string,
	input, output any,
	headers map[string]string,
) error {
	var body io.Reader
	if input != nil {
		encoded, err := json.Marshal(input)
		if err != nil {
			return err
		}
		body = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint, body)
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", "Bearer "+c.token)
	if input != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	response, err := c.http.Do(request)
	if err != nil {
		return fmt.Errorf("Hub communication failed: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		var problem struct {
			Detail    string `json:"detail"`
			RequestID string `json:"request_id"`
		}
		_ = json.NewDecoder(io.LimitReader(response.Body, 64*1024)).Decode(&problem)
		if problem.Detail == "" {
			problem.Detail = response.Status
		}
		// O corpo so traz request_id nos erros de dominio. Os recusados na
		// borda -- credencial invalida, TLS ausente -- respondem sem ele, e
		// sao justamente os mais dificeis de investigar. O cabecalho existe em
		// toda resposta, entao serve de segunda fonte.
		requestID := sanitizeRequestID(problem.RequestID)
		if requestID == "" {
			requestID = sanitizeRequestID(response.Header.Get(correlationHeader))
		}
		return &HTTPError{
			StatusCode: response.StatusCode,
			Detail:     problem.Detail,
			RequestID:  requestID,
		}
	}
	if output == nil || response.StatusCode == http.StatusNoContent {
		return nil
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 2*1024*1024)).Decode(output); err != nil {
		return fmt.Errorf("invalid response from Hub: %w", err)
	}
	return nil
}

// PublishedContent é o corpo revisável de uma publicação, sem o frontmatter, e o
// hash que precisa voltar em If-Match. O Hub gera o frontmatter e rejeita o que
// vier do cliente; devolvê-lo aqui só convidaria a colá-lo de volta.
type PublishedContent struct {
	Markdown    string `json:"markdown"`
	ContentHash string `json:"content_hash"`
}

func (c *Client) PublishedContent(
	ctx context.Context, runbookID string,
) (PublishedContent, error) {
	var response PublishedContent
	err := c.doJSON(
		ctx,
		http.MethodGet,
		c.endpoint("runbooks", runbookID, "content"),
		nil,
		&response,
		nil,
	)
	return response, err
}

func (c *Client) ReviseRunbook(
	ctx context.Context, runbookID, markdown, contentHash, key string, assets []Asset,
) (Job, error) {
	var response Job
	payload := struct {
		Markdown string  `json:"markdown"`
		Assets   []Asset `json:"assets,omitempty"`
	}{Markdown: markdown, Assets: assets}
	err := c.doJSON(
		ctx,
		http.MethodPost,
		c.endpoint("runbooks", runbookID, "revisions"),
		payload,
		&response,
		map[string]string{
			"Idempotency-Key": key,
			// Aspas fazem parte do contrato do header; sem elas o Hub responde 400.
			"If-Match": fmt.Sprintf("%q", contentHash),
		},
	)
	return response, err
}

// PublishedCatalog lista os IDs de runbooks publicados que podem ser
// revisados. So o UUID -- sem nome ou dominio -- porque essa e a mesma
// granularidade que o Hub garante hoje (GET /runbooks/published).
func (c *Client) PublishedCatalog(ctx context.Context) ([]string, error) {
	var response struct {
		IDs []string `json:"ids"`
	}
	err := c.doJSON(
		ctx,
		http.MethodGet,
		c.endpoint("runbooks", "published"),
		nil,
		&response,
		nil,
	)
	return response.IDs, err
}

// PublishedRunbooksMine lista so os IDs publicados que a identidade atual
// pode de fato revisar (mesma area, ou qualquer uma se for admin). E o que
// o app grafico usa para a aba de publicados -- PublishedCatalog devolve o
// catalogo inteiro, sem filtro de area.
// PublishedRunbookSummary é um runbook publicado que a identidade autenticada
// está autorizada a revisar de verdade -- id mais nome, para exibição sem
// obrigar quem lê a decorar UUIDs.
type PublishedRunbookSummary struct {
	ID   string
	Name string
}

func (c *Client) PublishedRunbooksMine(ctx context.Context) ([]PublishedRunbookSummary, error) {
	var response struct {
		IDs   []string          `json:"ids"`
		Names map[string]string `json:"names"`
	}
	err := c.doJSON(
		ctx,
		http.MethodGet,
		c.endpoint("runbooks", "published", "mine"),
		nil,
		&response,
		nil,
	)
	if err != nil {
		return nil, err
	}
	summaries := make([]PublishedRunbookSummary, len(response.IDs))
	for index, id := range response.IDs {
		summaries[index] = PublishedRunbookSummary{ID: id, Name: response.Names[id]}
	}
	return summaries, nil
}
