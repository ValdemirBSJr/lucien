package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"time"

	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"

	"github.com/lucien-runbook/lucien/desktop/connection"
	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/config"
	"github.com/lucien-runbook/lucien/internal/runbookdraft"
)

// App expõe ao frontend só o que as telas precisam -- login, configuração de
// conexão e (nas próximas etapas) os runbooks. Toda a lógica de rede,
// idempotência e armazenamento de credencial vem de cli/internal/*, igual ao
// CLI de terminal; este arquivo é só a superfície gráfica por cima dela.
type App struct {
	ctx context.Context
}

func NewApp() *App {
	return &App{}
}

func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
}

// ConnectionSettings é o que a tela de Configurações lê e grava.
type ConnectionSettings struct {
	APIHost string `json:"apiHost"`
	CAFile  string `json:"caFile"`
}

func (a *App) GetConnectionSettings() (ConnectionSettings, error) {
	settings, err := connection.Load()
	if err != nil {
		return ConnectionSettings{}, err
	}
	return ConnectionSettings{APIHost: settings.APIHost, CAFile: settings.CAFile}, nil
}

func (a *App) SaveConnectionSettings(apiHost, caFile string) error {
	return connection.Save(connection.Settings{APIHost: apiHost, CAFile: caFile})
}

// IsConnectionConfigured deixa o frontend distinguir "ainda nao configurado"
// de um erro de autenticacao de verdade, sem depender do texto da mensagem.
func (a *App) IsConnectionConfigured() (bool, error) {
	settings, err := connection.Load()
	if err != nil {
		return false, err
	}
	return settings.APIHost != "" && settings.CAFile != "", nil
}

// PickCAFile abre o seletor nativo do SO -- digitar caminho de arquivo a
// mao e o tipo de coisa que o CLI aceita por ser terminal, nao por ser bom.
func (a *App) PickCAFile() (string, error) {
	return wailsruntime.OpenFileDialog(a.ctx, wailsruntime.OpenDialogOptions{
		Title: "Selecione o arquivo de CA (TLS_CA_FILE)",
		Filters: []wailsruntime.FileFilter{
			{DisplayName: "Certificados (*.pem, *.crt)", Pattern: "*.pem;*.crt"},
			{DisplayName: "Todos os arquivos (*.*)", Pattern: "*.*"},
		},
	})
}

// Identity é o que a UI mostra depois de autenticado.
type Identity struct {
	ID             string   `json:"id"`
	Username       string   `json:"username"`
	RoleLevel      string   `json:"roleLevel"`
	DomainFunction string   `json:"domainFunction"`
	ExtraDomains   []string `json:"extraDomains"`
}

func identityFrom(identity api.UserIdentity) Identity {
	return Identity{
		ID:             identity.ID,
		Username:       identity.Username,
		RoleLevel:      identity.RoleLevel,
		DomainFunction: identity.DomainFunction,
		ExtraDomains:   identity.ExtraDomains,
	}
}

// loadedConnection devolve a configuracao de conexao ou um erro claro quando
// a tela de Configuracoes ainda nao foi preenchida -- o app nao tem variavel
// de ambiente como o CLI de terminal para cair de volta.
func loadedConnection() (connection.Settings, error) {
	settings, err := connection.Load()
	if err != nil {
		return connection.Settings{}, err
	}
	if settings.APIHost == "" || settings.CAFile == "" {
		return connection.Settings{}, errors.New("connection is not configured")
	}
	return settings, nil
}

// authenticatedClient monta o mesmo cliente que AuthStatus e as telas de
// runbook usam -- conexao configurada mais a credencial salva no keyring.
func (a *App) authenticatedClient() (*api.Client, error) {
	settings, err := loadedConnection()
	if err != nil {
		return nil, err
	}
	_, token, err := config.LoadAuthenticatedProfile(settings.APIHost)
	if err != nil {
		return nil, err
	}
	return api.NewClient(settings.APIHost, token, settings.CAFile)
}

// AuthStatus confirma a credencial salva sem pedir nada ao operador -- usado
// ao abrir o app para decidir entre a tela de Login e a de runbooks.
func (a *App) AuthStatus() (Identity, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return Identity{}, err
	}
	identity, err := client.Me(a.ctx)
	if err != nil {
		return Identity{}, err
	}
	return identityFrom(identity), nil
}

// Login aceita token provisorio ou permanente, exatamente como `lucien
// login`: um `luc_tmp_` e trocado por um permanente antes de salvar. A
// credencial final fica no keyring do SO (cli/internal/config), nunca
// devolvida ao frontend -- diferente do CLI, que so mostra o token uma vez
// porque roda num terminal sem outro lugar para guarda-lo.
func (a *App) Login(token string) (Identity, error) {
	settings, err := loadedConnection()
	if err != nil {
		return Identity{}, err
	}
	trimmed := strings.TrimSpace(token)
	if trimmed == "" {
		return Identity{}, errors.New("token cannot be empty")
	}
	client, err := api.NewClient(settings.APIHost, trimmed, settings.CAFile)
	if err != nil {
		return Identity{}, err
	}

	finalToken := trimmed
	var identity api.UserIdentity
	if strings.HasPrefix(trimmed, "luc_tmp_") {
		issued, err := client.ExchangeProvisionalToken(a.ctx)
		if err != nil {
			// Diz qual caminho foi tentado -- "credencial invalida" sozinho
			// nao distingue "token provisorio recusado" de "token permanente
			// nao reconhecido", e essa distincao e o primeiro passo do
			// diagnostico.
			return Identity{}, fmt.Errorf("exchanging provisional token: %w", err)
		}
		finalToken = issued.APIToken
		identity = api.UserIdentity{
			ID:             issued.ID,
			Username:       issued.Username,
			RoleLevel:      issued.RoleLevel,
			DomainFunction: issued.DomainFunction,
			IsActive:       issued.IsActive,
		}
	} else {
		identity, err = client.Me(a.ctx)
		if err != nil {
			return Identity{}, fmt.Errorf(
				"signing in with permanent token (no luc_tmp_ prefix found): %w", err,
			)
		}
	}

	if err := config.SaveAuthenticatedProfile(config.Profile{
		UserID:   identity.ID,
		Username: identity.Username,
	}, settings.APIHost, finalToken); err != nil {
		return Identity{}, err
	}
	return identityFrom(identity), nil
}

// Logout esquece só a credencial -- host e CA continuam configurados, então
// entrar de novo (com outro token) não pede os dois de volta.
func (a *App) Logout() error {
	settings, err := connection.Load()
	if err != nil {
		return err
	}
	if settings.APIHost == "" {
		return nil
	}
	return config.Forget(settings.APIHost)
}

// ForgetEverything desfaz o app inteiro para o estado de primeira execução:
// credencial, endereço do Hub e arquivo de CA. Depois dela, a próxima tela é
// sempre a de configuração de conexão.
func (a *App) ForgetEverything() error {
	settings, err := connection.Load()
	if err == nil && settings.APIHost != "" {
		if err := config.Forget(settings.APIHost); err != nil {
			return err
		}
	}
	return connection.Forget()
}

// RunbookRow é a linha da tabela da tela inicial -- um subconjunto de api.Job
// que a UI consegue renderizar sem reimportar o tipo inteiro do CLI.
type RunbookRow struct {
	ID              string `json:"id"`
	Name            string `json:"name"`
	Status          string `json:"status"`
	Description     string `json:"description"`
	CreatedAt       string `json:"createdAt"`
	ProcessingError string `json:"processingError"`
}

func runbookRowFrom(job api.Job) RunbookRow {
	return RunbookRow{
		ID:              job.ID,
		Name:            job.Name,
		Status:          job.Status,
		Description:     job.Description,
		CreatedAt:       job.CreatedAt.Format(time.RFC3339),
		ProcessingError: job.ProcessingError,
	}
}

// ListActiveRunbooks traz só o que ainda exige atenção do operador --
// PENDING, PROCESSING ou FAILED. Um runbook PUBLISHED nunca aparece aqui.
func (a *App) ListActiveRunbooks() ([]RunbookRow, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return nil, err
	}
	jobs, err := client.Active(a.ctx)
	if err != nil {
		return nil, err
	}
	rows := make([]RunbookRow, 0, len(jobs))
	for _, job := range jobs {
		rows = append(rows, runbookRowFrom(job))
	}
	return rows, nil
}

// ListDomainFunctions alimenta o seletor de área opcional do formulário de
// novo runbook, com a mesma lista que `lucien start -r` aceita.
func (a *App) ListDomainFunctions() ([]string, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return nil, err
	}
	configuration, err := client.RunbookConfiguration(a.ctx)
	if err != nil {
		return nil, err
	}
	return configuration.DomainFunctions, nil
}

// CreateRunbook passa pela mesma rota de upload do CLI de terminal -- o
// runbook nasce PROCESSING (desabilitado na tabela) até o Hub terminar o
// enriquecimento e o virar PENDING (editável) ou FAILED (com retry).
func (a *App) CreateRunbook(
	name, rawLog, description, domainFunction string,
) (RunbookRow, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return RunbookRow{}, err
	}
	job, err := client.Upload(a.ctx, name, rawLog, description, false, domainFunction)
	if err != nil {
		return RunbookRow{}, err
	}
	return runbookRowFrom(job), nil
}

// RetryRunbook reprocessa um runbook FAILED com a mesma entrada original.
func (a *App) RetryRunbook(id string) (RunbookRow, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return RunbookRow{}, err
	}
	job, err := client.RetryJob(a.ctx, id, false)
	if err != nil {
		return RunbookRow{}, err
	}
	return runbookRowFrom(job), nil
}

// DeleteRunbook remove o job. `force` é obrigatório para um PROCESSING --
// sem ele o Hub recusa apagar algo que ainda pode estar em andamento.
func (a *App) DeleteRunbook(id string, force bool) error {
	client, err := a.authenticatedClient()
	if err != nil {
		return err
	}
	return client.DeleteJob(a.ctx, id, force)
}

// PublishedRunbookSummary é um runbook publicado, com o nome para exibição.
type PublishedRunbookSummary struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// ListPublishedMine devolve os runbooks publicados que esta identidade está
// autorizada a revisar de verdade -- filtrado por área.
func (a *App) ListPublishedMine() ([]PublishedRunbookSummary, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return nil, err
	}
	summaries, err := client.PublishedRunbooksMine(a.ctx)
	if err != nil {
		return nil, err
	}
	rows := make([]PublishedRunbookSummary, len(summaries))
	for index, summary := range summaries {
		rows[index] = PublishedRunbookSummary{ID: summary.ID, Name: summary.Name}
	}
	return rows, nil
}

// RunbookDetail é o que a tela de edição carrega antes de montar o rascunho
// -- os comandos capturados, suas saídas, e as sugestões da SLM.
type RunbookDetail struct {
	ID                        string   `json:"id"`
	Name                      string   `json:"name"`
	Status                    string   `json:"status"`
	Description               string   `json:"description"`
	Commands                  []string `json:"commands"`
	CommandOutputs            []string `json:"commandOutputs"`
	Objective                 string   `json:"objective"`
	ArchitecturePrerequisites []string `json:"architecturePrerequisites"`
	CommandImpacts            []string `json:"commandImpacts"`
	RollbackCommands          []string `json:"rollbackCommands"`
}

// GetRunbookDetail carrega o job para a tela de edição decidir quais
// comandos exibir para seleção.
func (a *App) GetRunbookDetail(id string) (RunbookDetail, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return RunbookDetail{}, err
	}
	job, err := client.GetJob(a.ctx, id)
	if err != nil {
		return RunbookDetail{}, err
	}
	return RunbookDetail{
		ID:                        job.ID,
		Name:                      job.Name,
		Status:                    job.Status,
		Description:               job.Description,
		Commands:                  job.Commands,
		CommandOutputs:            job.CommandOutputs,
		Objective:                 job.RunbookSuggestions.Objective,
		ArchitecturePrerequisites: job.RunbookSuggestions.ArchitecturePrerequisites,
		CommandImpacts:            job.RunbookSuggestions.CommandImpacts,
		RollbackCommands:          job.RunbookSuggestions.RollbackCommands,
	}, nil
}

// GenerateRunbookDraft monta o mesmo modelo Markdown que `lucien job` monta
// no terminal (internal/runbookdraft), a partir dos comandos que o operador
// marcou como úteis nesta tela.
func (a *App) GenerateRunbookDraft(id string, selectedCommands []string) (string, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return "", err
	}
	job, err := client.GetJob(a.ctx, id)
	if err != nil {
		return "", err
	}
	configuration, err := client.RunbookConfiguration(a.ctx)
	if err != nil {
		return "", err
	}
	steps := runbookdraft.SelectedCommandSteps(
		job.Commands, job.CommandOutputs, job.RunbookSuggestions.CommandImpacts, selectedCommands,
	)
	template, err := runbookdraft.MarkdownTemplate(
		runbookdraft.DisplayName(job.Name), job.ID, steps, job.RunbookSuggestions,
		job.Description, configuration.Language,
	)
	if err != nil {
		return "", err
	}
	return string(template), nil
}

// PublishRunbook envia o markdown revisado. A idempotency key deriva de
// usuário+job+conteúdo -- reenviar o mesmo texto depois de uma falha de rede
// não publica em duplicidade, igual ao `lucien job sent` do terminal.
func (a *App) PublishRunbook(id, markdown string) (RunbookRow, error) {
	settings, err := loadedConnection()
	if err != nil {
		return RunbookRow{}, err
	}
	profile, token, err := config.LoadAuthenticatedProfile(settings.APIHost)
	if err != nil {
		return RunbookRow{}, err
	}
	client, err := api.NewClient(settings.APIHost, token, settings.CAFile)
	if err != nil {
		return RunbookRow{}, err
	}
	digest := sha256.Sum256([]byte(profile.UserID + "\x00" + id + "\x00" + markdown))
	job, err := client.Publish(a.ctx, id, markdown, hex.EncodeToString(digest[:]), nil)
	if err != nil {
		return RunbookRow{}, err
	}
	return runbookRowFrom(job), nil
}
