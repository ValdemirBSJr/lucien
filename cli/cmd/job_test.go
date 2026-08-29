package cmd

import (
	"bytes"
	"context"
	"errors"
	"github.com/lucien-runbook/lucien/internal/draft"
	"strings"
	"testing"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/spf13/cobra"
)

type fakeActiveJobLister struct {
	jobs  []api.Job
	err   error
	calls int
}

func (fake *fakeActiveJobLister) Active(context.Context) ([]api.Job, error) {
	fake.calls++
	return fake.jobs, fake.err
}

func TestMarkdownTemplateMantemCabecalhoEBlocoBashAdjacentes(t *testing.T) {
	template, err := markdownTemplate(
		"Revisão",
		"12345678-1234-1234-1234-123456789abc",
		[]commandStep{{command: "docker ps"}, {command: "kubectl get pods"}},
		api.RunbookSuggestions{},
		"",
		"en",
	)
	if err != nil {
		t.Fatalf("gerar template em inglês: %v", err)
	}
	content := string(template)

	for _, expected := range []string{
		"MANDATORY REVIEW — CAPTURED COMMAND",
		"### Step 1: Run selected command\n```bash\ndocker ps\n```",
		"### Step 2: Run selected command\n```bash\nkubectl get pods\n```",
	} {
		if !strings.Contains(content, expected) {
			t.Fatalf("template não respeita gramática de chunking: %q", expected)
		}
	}
}

func TestMarkdownTemplateEmPortugues(t *testing.T) {
	template, err := markdownTemplate(
		"Revisão",
		"12345678-1234-1234-1234-123456789abc",
		[]commandStep{{command: "docker ps"}},
		api.RunbookSuggestions{},
		"",
		"pt-br",
	)
	if err != nil {
		t.Fatalf("gerar template em português: %v", err)
	}
	content := string(template)
	for _, expected := range []string{
		"## Objetivo",
		"### Passo 1: Executar comando selecionado\n```bash\ndocker ps\n```",
		"## Validação",
	} {
		if !strings.Contains(content, expected) {
			t.Fatalf("template em português incompleto: %q", expected)
		}
	}
}

func TestMarkdownTemplateContemSomenteComandosConfirmados(t *testing.T) {
	template, err := markdownTemplate(
		"Revisão",
		"12345678-1234-1234-1234-123456789abc",
		[]commandStep{{command: "date"}, {command: "nproc"}},
		api.RunbookSuggestions{},
		"",
		"pt-br",
	)
	if err != nil {
		t.Fatalf("gerar template: %v", err)
	}
	content := string(template)
	if strings.Contains(content, "uptime") {
		t.Fatal("template incluiu comando não selecionado")
	}
	for _, selected := range []string{"date", "nproc"} {
		if count := strings.Count(content, "\n"+selected+"\n"); count != 1 {
			t.Fatalf("comando %q apareceu %d vezes", selected, count)
		}
	}
}

func TestDisplayJobNameRemoveSomenteSufixoGeradoPeloLucien(t *testing.T) {
	name := "teste-uso_1-20260813-001602-7093b5c3e42d"
	if got := displayJobName(name); got != "teste-uso_1" {
		t.Fatalf("nome limpo inesperado: %q", got)
	}
	custom := "teste-uso_1-manual"
	if got := displayJobName(custom); got != custom {
		t.Fatalf("nome não gerado foi alterado: %q", got)
	}
}

func TestMarkdownTemplateRejeitaIdiomaDesconhecido(t *testing.T) {
	_, err := markdownTemplate("job", "job-id", nil, api.RunbookSuggestions{}, "", "es")
	if err == nil {
		t.Fatal("idioma desconhecido deveria ser rejeitado")
	}
}

func TestTemplateIncluiSaidaSomenteDosComandosSelecionados(t *testing.T) {
	steps := selectedCommandSteps(
		[]string{"uptime", "date", "nproc"},
		[]string{"saida uptime", "saida date", "saida nproc"},
		[]string{"impacto uptime", "impacto date", "impacto nproc"},
		[]string{"date", "nproc"},
	)
	template, err := markdownTemplate(
		"job", "job-id", steps, api.RunbookSuggestions{}, "", "pt-br",
	)
	if err != nil {
		t.Fatalf("gerar template: %v", err)
	}
	content := string(template)
	if strings.Contains(content, "uptime") {
		t.Fatal("template incluiu comando ou saída desmarcada")
	}
	for _, expected := range []string{
		"```bash\ndate\n```\n```text\nsaida date\n```",
		"```bash\nnproc\n```\n```text\nsaida nproc\n```",
	} {
		if !strings.Contains(content, expected) {
			t.Fatalf("saída selecionada ausente: %q", expected)
		}
	}
}

func TestTemplateAceitaJobAntigoSemSaidas(t *testing.T) {
	steps := selectedCommandSteps([]string{"date"}, nil, nil, []string{"date"})
	template, err := markdownTemplate(
		"job", "job-id", steps, api.RunbookSuggestions{}, "", "en",
	)
	if err != nil {
		t.Fatalf("gerar template legado: %v", err)
	}
	if strings.Contains(string(template), "```text") {
		t.Fatal("job sem saída não deveria gerar bloco vazio")
	}
}

func TestOutputFenceNaoEQuebradoPelaSaida(t *testing.T) {
	template, err := markdownTemplate(
		"job",
		"job-id",
		[]commandStep{{command: "printf", output: "```bash\nconteúdo\n```"}},
		api.RunbookSuggestions{},
		"",
		"en",
	)
	if err != nil {
		t.Fatalf("gerar template: %v", err)
	}
	if !strings.Contains(string(template), "````text\n```bash\nconteúdo\n```\n````") {
		t.Fatal("fence da saída não protegeu Markdown aninhado")
	}
}

func TestTemplateUsaSugestoesRevisaveisDaSLM(t *testing.T) {
	suggestions := api.RunbookSuggestions{
		Objective:                 "Validar a disponibilidade do serviço.",
		ArchitecturePrerequisites: []string{"Acesso ao host alvo."},
		CommandImpacts:            []string{"Consulta sem alteração de estado."},
		RollbackCommands:          []string{"systemctl restart nginx"},
	}
	steps := selectedCommandSteps(
		[]string{"systemctl status nginx"},
		[]string{"active (running)"},
		suggestions.CommandImpacts,
		[]string{"systemctl status nginx"},
	)
	template, err := markdownTemplate("nginx", "job-id", steps, suggestions, "", "pt-br")
	if err != nil {
		t.Fatalf("gerar template: %v", err)
	}
	content := string(template)
	for _, expected := range []string{
		"Validar a disponibilidade do serviço.",
		"- Acesso ao host alvo.",
		"REVISÃO OBRIGATÓRIA — SUGESTÃO DA SLM",
		"possível impacto: Consulta sem alteração de estado.",
		"```sh\nsystemctl restart nginx\n```",
	} {
		if !strings.Contains(content, expected) {
			t.Fatalf("sugestão ausente no template: %q", expected)
		}
	}
	impactPosition := strings.Index(content, "possível impacto:")
	commandPosition := strings.Index(content, "### Passo 1:")
	if impactPosition < 0 || commandPosition < 0 || impactPosition > commandPosition {
		t.Fatal("aviso obrigatório de impacto deve aparecer antes do comando")
	}
	reviewPosition := strings.Index(content, "COMANDO CAPTURADO")
	if reviewPosition < 0 || reviewPosition > commandPosition {
		t.Fatal("todo comando deve possuir aviso de revisão antes do passo")
	}
}

func TestStartExpõeDescribeOpcionalERecomendado(t *testing.T) {
	command := newStartCommand()
	flag := command.Flags().Lookup("describe")
	if flag == nil {
		t.Fatal("flag --describe não registrada")
	}
	if flag.Shorthand != "d" {
		t.Fatalf("atalho inesperado: %q", flag.Shorthand)
	}
	if flag.DefValue != "" {
		t.Fatalf("describe deve ser opcional; valor padrão: %q", flag.DefValue)
	}
	if !strings.Contains(strings.ToLower(flag.Usage), "recommended") {
		t.Fatalf("help deve recomendar a descrição: %q", flag.Usage)
	}
}

func TestRetryRecusaJobEmProcessamento(t *testing.T) {
	err := validateRetryableJob(api.Job{ID: "job-processing", Status: "PROCESSING"})
	if err == nil || !strings.Contains(err.Error(), "only for FAILED") {
		t.Fatalf("retry deveria recusar PROCESSING: %v", err)
	}
}

func TestRetryAceitaSomenteJobFailed(t *testing.T) {
	if err := validateRetryableJob(api.Job{ID: "job-failed", Status: "FAILED"}); err != nil {
		t.Fatalf("retry deveria aceitar FAILED: %v", err)
	}
}

func TestDeleteExpoeForceParaCancelarProcessamento(t *testing.T) {
	command := newJobDeleteCommand()
	flag := command.Flags().Lookup("force")
	if flag == nil || flag.Shorthand != "f" {
		t.Fatal("job del deve expor --force/-f")
	}
	if !strings.Contains(flag.Usage, "PROCESSING") {
		t.Fatalf("help do force não informa o escopo: %q", flag.Usage)
	}
}

func TestResolveJobIndexUsaMesmaOrdemDaLista(t *testing.T) {
	jobs := []api.Job{{ID: "primeiro"}, {ID: "segundo"}}

	identifier, err := resolveJobIndex("2", jobs)
	if err != nil {
		t.Fatalf("resolver índice: %v", err)
	}
	if identifier != "segundo" {
		t.Fatalf("job inesperado: %q", identifier)
	}
}

func TestResolveJobIndexPreservaIDOuNome(t *testing.T) {
	identifier, err := resolveJobIndex("job-existente", nil)
	if err != nil {
		t.Fatalf("preservar identificador: %v", err)
	}
	if identifier != "job-existente" {
		t.Fatalf("identificador inesperado: %q", identifier)
	}
}

func TestResolveJobIndexRejeitaPosicaoInexistente(t *testing.T) {
	_, err := resolveJobIndex("3", []api.Job{{ID: "primeiro"}, {ID: "segundo"}})
	if err == nil || !strings.Contains(err.Error(), "outside the reviews list") {
		t.Fatalf("índice fora da lista deveria falhar claramente: %v", err)
	}
}

func TestResolveJobIndexInformaListaVazia(t *testing.T) {
	_, err := resolveJobIndex("1", nil)
	if err == nil || !strings.Contains(err.Error(), "reviews list is empty") {
		t.Fatalf("lista vazia deveria falhar claramente: %v", err)
	}
}

func TestResolveJobIdentifierConsultaReviewsSomenteParaIndice(t *testing.T) {
	lister := &fakeActiveJobLister{
		jobs: []api.Job{{ID: "primeiro"}, {ID: "segundo"}},
	}

	identifier, err := resolveJobIdentifier(context.Background(), lister, "2")
	if err != nil {
		t.Fatalf("resolver índice: %v", err)
	}
	if identifier != "segundo" || lister.calls != 1 {
		t.Fatalf("resolução inesperada: id=%q chamadas=%d", identifier, lister.calls)
	}

	identifier, err = resolveJobIdentifier(
		context.Background(), lister, "job-existente",
	)
	if err != nil {
		t.Fatalf("preservar ID: %v", err)
	}
	if identifier != "job-existente" || lister.calls != 1 {
		t.Fatalf("ID causou consulta desnecessária: id=%q chamadas=%d", identifier, lister.calls)
	}
}

func TestResolveJobIdentifierPropagaFalhaDaLista(t *testing.T) {
	expected := errors.New("Hub indisponível")
	lister := &fakeActiveJobLister{err: expected}

	_, err := resolveJobIdentifier(context.Background(), lister, "1")
	if !errors.Is(err, expected) {
		t.Fatalf("falha inesperada: %v", err)
	}
}

func TestSubcomandosDeJobDocumentamIndice(t *testing.T) {
	commands := []*cobra.Command{
		newJobStatusCommand(),
		newJobRetryCommand(),
		newJobSentCommand(),
		newJobDeleteCommand(),
	}
	for _, command := range commands {
		if !strings.Contains(command.Use, "review_index") {
			t.Errorf("%s não documenta índice: %q", command.Name(), command.Use)
		}
	}
}

func TestTemplateUsaDescricaoQuandoNaoHaEnriquecimento(t *testing.T) {
	template, err := markdownTemplate(
		"job",
		"job-id",
		[]commandStep{{command: "ls"}},
		api.RunbookSuggestions{},
		"Diagnosticar latência no cache Redis",
		"pt-br",
	)
	if err != nil {
		t.Fatalf("gerar template: %v", err)
	}
	content := string(template)
	// A descrição virou subtítulo do objetivo; antes era citação.
	if !strings.Contains(content, "### Diagnosticar latência no cache Redis") {
		t.Fatal("a descrição da captura deveria preencher o objetivo")
	}
	if !strings.Contains(content, "DESCRIÇÃO DO OPERADOR") {
		t.Fatal("a descrição deveria ser rotulada como texto do operador")
	}
	if strings.Contains(content, "SUGESTÃO DA SLM:** valide e ajuste") {
		t.Fatal("descrição do operador não pode ser rotulada como sugestão da SLM")
	}
	for _, esperado := range []string{"## Validação", "## Rollback", "```bash\nls\n```"} {
		if !strings.Contains(content, esperado) {
			t.Fatalf("estrutura básica ausente: %q", esperado)
		}
	}
}

func TestEnriquecimentoTemPrecedenciaSobreDescricao(t *testing.T) {
	template, err := markdownTemplate(
		"job",
		"job-id",
		[]commandStep{{command: "ls"}},
		api.RunbookSuggestions{Objective: "Objetivo sugerido pela SLM"},
		"descricao do operador",
		"pt-br",
	)
	if err != nil {
		t.Fatalf("gerar template: %v", err)
	}
	content := string(template)
	if !strings.Contains(content, "Objetivo sugerido pela SLM") {
		t.Fatal("o objetivo da SLM deveria prevalecer")
	}
	if strings.Contains(content, "descricao do operador") {
		t.Fatal("a descrição não deveria aparecer quando há enriquecimento")
	}
}

func TestObjetivoUsaDescricaoComoSubtitulo(t *testing.T) {
	rascunho, err := markdownTemplate(
		"zte-olt-rota-down",
		"3e381ebe-0284-4d3b-b304-a13655e3dd4c",
		[]commandStep{{command: "show ip route"}},
		api.RunbookSuggestions{},
		"Comandos para verificação de rota down nas OLT's ZTE",
		"pt-br",
	)
	if err != nil {
		t.Fatalf("montar rascunho: %v", err)
	}
	texto := string(rascunho)

	titulo := "### Comandos para verificação de rota down nas OLT's ZTE"
	nota := "> **REVISÃO OBRIGATÓRIA — DESCRIÇÃO DO OPERADOR:**"
	if !strings.Contains(texto, titulo) {
		t.Fatalf("descrição não virou subtítulo:\n%s", texto)
	}
	// O título vem antes da nota: é o assunto do runbook, e a nota é onde o
	// operador escreve o objetivo.
	if strings.Index(texto, titulo) > strings.Index(texto, nota) {
		t.Fatalf("nota apareceu antes do subtítulo:\n%s", texto)
	}
	// E logo abaixo de "## Objetivo", não solto no documento.
	if strings.Index(texto, "## Objetivo") > strings.Index(texto, titulo) {
		t.Fatalf("subtítulo apareceu antes da seção:\n%s", texto)
	}
}

func TestSingleLineAchataDescricaoMultilinha(t *testing.T) {
	// Uma quebra de linha partiria o cabeçalho Markdown ao meio.
	if got := singleLine("linha um\nlinha dois\t e   tres"); got != "linha um linha dois e tres" {
		t.Fatalf("descrição não foi achatada: %q", got)
	}
}

func TestJobRetomaRascunhoSalvo(t *testing.T) {
	// Depois de um `job sent` recusado, o operador roda `lucien job <id>` de
	// novo para corrigir. Regenerar o modelo por cima apagaria a revisao
	// inteira -- foi o que aconteceu com o bloqueio da politica de segredos.
	comando := newJobCommand()
	flag := comando.Flags().Lookup("reset")
	if flag == nil {
		t.Fatal("a saida do rascunho salvo precisa de --reset")
	}
	if flag.DefValue != "false" {
		t.Fatalf("--reset nao pode ser o padrao: %q", flag.DefValue)
	}
	if !strings.Contains(flag.Usage, "draft") {
		t.Fatalf("a ajuda de --reset nao explica o efeito: %q", flag.Usage)
	}
}

func TestJobCatImprimeORascunhoLocalSemFalarComOHub(t *testing.T) {
	// O rascunho recusado nunca chegou ao Hub, e e justamente o que interessa
	// ler. Com UUID completo o comando nao precisa de rede nenhuma: um
	// servidor que responda aqui e falha do teste.
	estado := t.TempDir()
	t.Setenv("XDG_STATE_HOME", estado)

	const id = "f51201f2-388a-4ce5-99ea-5d59f9424ca9"
	conteudo := []byte("# rascunho\n\n- Privilegio, senha LDAP/TACACS\n") // gitleaks:allow
	if err := draft.Save(id, conteudo); err != nil {
		t.Fatalf("salvar rascunho: %v", err)
	}

	comando := newJobCatCommand()
	var saida bytes.Buffer
	comando.SetOut(&saida)
	comando.SetArgs([]string{id})

	if err := comando.Execute(); err != nil {
		t.Fatalf("executar: %v", err)
	}
	if saida.String() != string(conteudo) {
		t.Fatalf("saida divergente:\n%q", saida.String())
	}
}

func TestJobCatFalhaQuandoNaoHaRascunho(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	comando := newJobCatCommand()
	comando.SetOut(&bytes.Buffer{})
	comando.SetArgs([]string{"11111111-1111-4111-8111-111111111111"})

	err := comando.Execute()
	if err == nil {
		t.Fatal("esperava erro para rascunho ausente")
	}
	if !strings.Contains(err.Error(), "draft not found") {
		t.Fatalf("mensagem inesperada: %v", err)
	}
}

func TestUUIDCompletoDistingueIndiceENome(t *testing.T) {
	// So o UUID dispensa o Hub. Indice e nome precisam da lista, entao nao
	// podem cair no caminho offline.
	aceitos := []string{
		"f51201f2-388a-4ce5-99ea-5d59f9424ca9",
		"F51201F2-388A-4CE5-99EA-5D59F9424CA9",
	}
	for _, valor := range aceitos {
		if !uuidCompleto.MatchString(valor) {
			t.Fatalf("deveria reconhecer %q", valor)
		}
	}
	recusados := []string{
		"1", "12", "heavy-user-cisco", "f51201f2", "f51201f2-388a",
		"f51201f2-388a-4ce5-99ea-5d59f9424ca9-extra",
	}
	for _, valor := range recusados {
		if uuidCompleto.MatchString(valor) {
			t.Fatalf("nao deveria reconhecer %q", valor)
		}
	}
}

func TestJobCatRecusaIndiceENomeSemConsultarOHub(t *testing.T) {
	// A recusa tem de vir do proprio comando, nao de uma tentativa de rede.
	// Sem API_HOST configurado, qualquer caminho que fale com o Hub falharia
	// com outro erro -- entao a mensagem exata e a prova de que nao foi la.
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	for _, entrada := range []string{"1", "heavy-user-cisco", "f51201f2"} {
		comando := newJobCatCommand()
		comando.SetOut(&bytes.Buffer{})
		comando.SetErr(&bytes.Buffer{})
		comando.SetArgs([]string{entrada})

		err := comando.Execute()
		if err == nil {
			t.Fatalf("esperava recusa para %q", entrada)
		}
		if !strings.Contains(err.Error(), "exact job ID") {
			t.Fatalf("para %q, mensagem inesperada: %v", entrada, err)
		}
	}
}
