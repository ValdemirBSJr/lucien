package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"os"
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

// AppInfo traz os mesmos tres dados que o Windows mostra nas propriedades do
// arquivo. O app precisa saber dize-los por conta propria: em Linux e macOS nao
// ha Explorer, e no Windows o recurso de versao nem sempre e gravado.
type AppInfo struct {
	ProductName string `json:"productName"`
	Version     string `json:"version"`
	Copyright   string `json:"copyright"`
}

func (a *App) GetAppInfo() AppInfo {
	return AppInfo{ProductName: productName, Version: version, Copyright: copyright}
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

// EditorAsset é uma imagem anexada a partir do editor, antes do gate de
// segurança do Hub (OCR + gitleaks) -- espelha api.Asset, num formato que o
// binding JS do Wails serializa direto.
type EditorAsset struct {
	Filename      string `json:"filename"`
	ContentBase64 string `json:"contentBase64"`
	MediaType     string `json:"mediaType"`
}

func toAPIAssets(assets []EditorAsset) []api.Asset {
	converted := make([]api.Asset, len(assets))
	for index, asset := range assets {
		converted[index] = api.Asset{
			Filename:      asset.Filename,
			ContentBase64: asset.ContentBase64,
			MediaType:     asset.MediaType,
		}
	}
	return converted
}

// ImportImage abre o seletor nativo de arquivo e devolve uma imagem
// PNG/JPEG pronta para anexar. O nome aqui só precisa ser único dentro desta
// publicação -- o Hub decide sozinho o nome final em disco (opaco, UUID),
// nunca o do arquivo original. Devolve EditorAsset{} sem erro quando o
// operador cancela o seletor.
func (a *App) ImportImage() (EditorAsset, error) {
	path, err := wailsruntime.OpenFileDialog(a.ctx, wailsruntime.OpenDialogOptions{
		Title: "Selecione uma imagem (PNG ou JPEG)",
		Filters: []wailsruntime.FileFilter{
			{DisplayName: "Imagens (*.png, *.jpg, *.jpeg)", Pattern: "*.png;*.jpg;*.jpeg"},
		},
	})
	if err != nil || path == "" {
		return EditorAsset{}, err
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return EditorAsset{}, err
	}
	return encodeImageAsset(content)
}

func encodeImageAsset(content []byte) (EditorAsset, error) {
	mediaType := http.DetectContentType(content)
	var extension string
	switch mediaType {
	case "image/png":
		extension = "png"
	case "image/jpeg":
		extension = "jpg"
	default:
		return EditorAsset{}, fmt.Errorf(
			"unsupported image type %q; only PNG and JPEG are accepted", mediaType,
		)
	}
	suffix := make([]byte, 6)
	if _, err := rand.Read(suffix); err != nil {
		return EditorAsset{}, err
	}
	filename := fmt.Sprintf("img-%s.%s", hex.EncodeToString(suffix), extension)
	return EditorAsset{
		Filename:      filename,
		ContentBase64: base64.StdEncoding.EncodeToString(content),
		MediaType:     mediaType,
	}, nil
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

// SessionProbe descreve o estado da sessão ao abrir o app.
//
// Existe porque "sem credencial" e "Hub inalcançável" caíam no mesmo caminho:
// qualquer erro virava a tela de token. Quem estava sem rede era mandado
// digitar um token, que não resolveria nada.
//
// Não carrega o texto do erro de propósito. O erro de transporte do Go traz a
// URL do Hub, e a tela de configuração mascara justamente esse endereço --
// exibi-lo aqui desfaria a máscara num banner de erro.
type SessionProbe struct {
	// Nulo quando não há sessão: o frontend testa a presença, não um campo
	// vazio que também seria um usuário sem nome.
	Identity    *Identity `json:"identity"`
	Unreachable bool      `json:"unreachable"`
}

// AuthStatus confirma a credencial salva sem pedir nada ao operador -- usado
// ao abrir o app para decidir entre a tela de Login, a de erro de conexão e a
// de runbooks. Não devolve erro: toda falha é um estado da sessão, e tratá-la
// como exceção foi o que misturou os dois casos.
func (a *App) AuthStatus() SessionProbe {
	client, err := a.authenticatedClient()
	if err != nil {
		// Sem credencial salva não há o que alcançar, e a tela de token é a
		// resposta certa mesmo sem rede.
		return SessionProbe{}
	}
	identity, err := client.Me(a.ctx)
	if err == nil {
		encontrada := identityFrom(identity)
		return SessionProbe{Identity: &encontrada}
	}
	// O Hub respondeu e recusou: credencial inválida, expirada ou revogada. A
	// tela de token resolve, e é para lá que o operador deve ir.
	var recusa *api.HTTPError
	if errors.As(err, &recusa) {
		return SessionProbe{}
	}
	// Não houve resposta: DNS, rota, TLS ou conexão recusada. Digitar um token
	// não muda nada disso.
	return SessionProbe{Unreachable: true}
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

// GenerateLocalDraft monta o rascunho do "Novo runbook" inteiramente no
// cliente, sem criar nada no Hub -- o job só nasce quando o operador publica
// de dentro do editor. Reconhece a sintaxe \@ (comando/saída) e, na
// ausência de qualquer marcador, inclui o texto como parágrafo comum; campo
// vazio não acrescenta nada, igual a um runbook puramente visual.
//
// A leitura do idioma no Hub é só um recurso a mais: sem conexão, cai em
// "pt-br" e o rascunho é montado do mesmo jeito -- o Hub só entra em cena de
// verdade quando a publicação acontece, exatamente o ponto em que ele já
// precisa estar disponível de qualquer forma.
func (a *App) GenerateLocalDraft(name, description, rawLog string) (string, error) {
	language := "pt-br"
	if client, err := a.authenticatedClient(); err == nil {
		if configuration, err := client.RunbookConfiguration(a.ctx); err == nil &&
			configuration.Language != "" {
			language = configuration.Language
		}
	}
	pairs, plainText := parseTypedLog(rawLog)
	steps := make([]runbookdraft.CommandStep, len(pairs))
	for index, pair := range pairs {
		steps[index] = runbookdraft.CommandStep{Command: pair.Command, Output: pair.Output}
	}
	template, err := runbookdraft.MarkdownTemplate(
		runbookdraft.DisplayName(name), "", steps, api.RunbookSuggestions{},
		description, language,
	)
	if err != nil {
		return "", err
	}
	if plainText != "" {
		template = insertPlainProcedureText(template, plainText)
	}
	return string(template), nil
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

// PublishedRunbookContent é o corpo revisável de uma publicação, mais o hash
// que a revisão precisa devolver em If-Match -- o Hub recusa qualquer outro.
type PublishedRunbookContent struct {
	Markdown    string `json:"markdown"`
	ContentHash string `json:"contentHash"`
}

// GetPublishedContent carrega o markdown de uma versão publicada para
// visualização ou como ponto de partida de uma revisão.
func (a *App) GetPublishedContent(id string) (PublishedRunbookContent, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return PublishedRunbookContent{}, err
	}
	content, err := client.PublishedContent(a.ctx, id)
	if err != nil {
		return PublishedRunbookContent{}, err
	}
	return PublishedRunbookContent{Markdown: content.Markdown, ContentHash: content.ContentHash}, nil
}

// ReviseRunbook publica um sucessor imutável. O Hub garante sozinho que só a
// ponta atual da linhagem pode ser revisada -- se `id` já tiver uma versão
// mais nova, a chamada volta com um erro apontando qual é a correta, em vez
// de aceitar uma revisão sobre uma versão superada.
func (a *App) ReviseRunbook(
	id, markdown, contentHash string, assets []EditorAsset,
) (RunbookRow, error) {
	client, err := a.authenticatedClient()
	if err != nil {
		return RunbookRow{}, err
	}
	key, err := api.NewIdempotencyKey()
	if err != nil {
		return RunbookRow{}, err
	}
	job, err := client.ReviseRunbook(a.ctx, id, markdown, contentHash, key, toAPIAssets(assets))
	if err != nil {
		return RunbookRow{}, err
	}
	return runbookRowFrom(job), nil
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
func (a *App) PublishRunbook(id, markdown string, assets []EditorAsset) (RunbookRow, error) {
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
	job, err := client.Publish(
		a.ctx, id, markdown, hex.EncodeToString(digest[:]), toAPIAssets(assets),
	)
	if err != nil {
		return RunbookRow{}, err
	}
	return runbookRowFrom(job), nil
}
