package cmd

import (
	"bytes"
	"os"
	"strings"
	"testing"
)

func TestHelpOcultaComandoDeCompletion(t *testing.T) {
	var saida bytes.Buffer
	root := NewRootCommand()
	root.SetOut(&saida)
	root.SetArgs([]string{"help"})

	if err := root.Execute(); err != nil {
		t.Fatalf("help retornou erro: %v", err)
	}
	if strings.Contains(saida.String(), "\n  completion ") {
		t.Fatalf("completion interno apareceu no help: %q", saida.String())
	}
}

func TestCompletionOcultoContinuaDisponivelParaInstalador(t *testing.T) {
	var saida bytes.Buffer
	root := NewRootCommand()
	root.SetOut(&saida)
	root.SetArgs([]string{"completion", "bash"})

	if err := root.Execute(); err != nil {
		t.Fatalf("completion interno retornou erro: %v", err)
	}
	if !strings.Contains(saida.String(), "bash completion") {
		t.Fatalf("script bash não foi gerado: %q", saida.String())
	}
}

func TestComandosDeIdentidadeAparecemNoHelp(t *testing.T) {
	var saida bytes.Buffer
	root := NewRootCommand()
	root.SetOut(&saida)
	root.SetArgs([]string{"help"})

	if err := root.Execute(); err != nil {
		t.Fatalf("help retornou erro: %v", err)
	}
	for _, command := range []string{"auth", "admin"} {
		if !strings.Contains(saida.String(), "\n  "+command+" ") {
			t.Fatalf("comando %s não apareceu no help: %q", command, saida.String())
		}
	}
}

func TestAdminExigeEscopoEConfirmacaoExplicitos(t *testing.T) {
	// Nivel e area sao exigidos antes de qualquer chamada de rede: criar um
	// usuario sem escopo definido e o tipo de erro que nao deve chegar ao Hub.
	semNivel := newAdminCreateUserCommand()
	semNivel.SetArgs([]string{"operador", "-r", "servidores"})
	if err := semNivel.Execute(); err == nil || !strings.Contains(err.Error(), "--level") {
		t.Fatalf("create sem --level deveria falhar antes da rede: %v", err)
	}

	semArea := newAdminCreateUserCommand()
	semArea.SetArgs([]string{"operador", "--level", "senior"})
	if err := semArea.Execute(); err == nil || !strings.Contains(err.Error(), "-r") {
		t.Fatalf("create sem -r deveria falhar antes da rede: %v", err)
	}

	revoke := newAdminRevokeUserCommand()
	revoke.SetArgs([]string{"operador"})
	if err := revoke.Execute(); err == nil || !strings.Contains(err.Error(), "--yes") {
		t.Fatalf("revoke sem confirmação deveria falhar antes da rede: %v", err)
	}
}

func TestLoginRejeitaTokenComoArgumento(t *testing.T) {
	login := newLoginCommand()
	login.SetArgs([]string{"luc_tmp_nao_deve_ir_para_o_historico"})

	if err := login.Execute(); err == nil {
		t.Fatal("login deveria rejeitar token passado como argumento")
	}
}

func TestLoginLeTokenDoStdinSemAceitarArgumento(t *testing.T) {
	command := newLoginCommand()
	command.SetIn(strings.NewReader("luc_tmp_seguro\n"))
	token, err := loginToken(command, true)
	if err != nil {
		t.Fatalf("token via stdin deveria ser aceito: %v", err)
	}
	if token != "luc_tmp_seguro" {
		t.Fatalf("token inesperado: %q", token)
	}
}

func TestLoginLimitaTokenRecebidoPorStdin(t *testing.T) {
	command := newLoginCommand()
	command.SetIn(strings.NewReader(strings.Repeat("x", 4097)))
	if _, err := loginToken(command, true); err == nil {
		t.Fatal("token acima do limite deveria ser rejeitado")
	}
}

func TestIdentidadeEsperadaDoJumpServerEObrigatoria(t *testing.T) {
	t.Setenv("LUCIEN_EXPECTED_USERNAME", "U000001")
	if err := validateExpectedIdentity("U000001"); err != nil {
		t.Fatalf("identidade correta foi rejeitada: %v", err)
	}
	if err := validateExpectedIdentity("Admin"); err == nil {
		t.Fatal("identidade diferente deveria ser rejeitada")
	}
}

func TestModoJumpProtegeOperacoesMasPreservaStop(t *testing.T) {
	root := NewRootCommand()
	for _, name := range []string{"start", "upload", "reviews", "job", "admin"} {
		command, _, err := root.Find([]string{name})
		if err != nil || !isJumpProtected(command) {
			t.Fatalf("comando %s deveria estar protegido", name)
		}
	}
	stop, _, err := root.Find([]string{"stop"})
	if err != nil || isJumpProtected(stop) {
		t.Fatal("stop precisa permanecer disponível para preservar a sessão")
	}
	_ = os.Unsetenv("LUCIEN_EXPECTED_USERNAME")
}

func TestTerminalPessoalNaoAtivaPoliticaDoJump(t *testing.T) {
	t.Setenv("LUCIEN_JUMP_MODE", "")
	root := NewRootCommand()
	start, _, err := root.Find([]string{"start"})
	if err != nil {
		t.Fatalf("comando start não foi encontrado: %v", err)
	}
	if shouldEnforceJumpAuthentication(start) {
		t.Fatal("terminal pessoal não deve exigir correlação com usuário POSIX")
	}

	t.Setenv("LUCIEN_JUMP_MODE", "true")
	if !shouldEnforceJumpAuthentication(start) {
		t.Fatal("modo jump deveria ativar a validação adicional")
	}
}

func TestParseRolesSeparaPrimariaDeAdicionais(t *testing.T) {
	primaria, extras, err := parseRoles("servidores, acessos ,roteamento")
	if err != nil {
		t.Fatalf("lista válida recusada: %v", err)
	}
	// A primeira é a primária: é ela que `lucien start` usa sem `-r`.
	if primaria != "servidores" {
		t.Fatalf("primária inesperada: %q", primaria)
	}
	if len(extras) != 2 || extras[0] != "acessos" || extras[1] != "roteamento" {
		t.Fatalf("adicionais inesperadas: %v", extras)
	}
}

func TestParseRolesRecusaAreaForaDaGramatica(t *testing.T) {
	for _, invalido := range []string{"", " , ", "servidores,Acessos", "servidores,ac"} {
		if _, _, err := parseRoles(invalido); err == nil {
			t.Fatalf("lista inválida foi aceita: %q", invalido)
		}
	}
}

func TestParseRolesDescartaRepeticao(t *testing.T) {
	primaria, extras, err := parseRoles("servidores,acessos,servidores")
	if err != nil {
		t.Fatalf("lista válida recusada: %v", err)
	}
	// A primária repetida não pode voltar como adicional: o Hub une os dois
	// conjuntos e a duplicata só poluiria a listagem do admin.
	if primaria != "servidores" || len(extras) != 1 || extras[0] != "acessos" {
		t.Fatalf("repetição não foi descartada: %q %v", primaria, extras)
	}
}

func TestVersaoEExpostaNoBinario(t *testing.T) {
	// Saber qual versao esta na maquina e a primeira pergunta quando um
	// comportamento diverge do esperado. Sem a injecao do build, `dev` ja
	// distingue binario local de pacote publicado.
	if Version() == "" {
		t.Fatal("a versao nao pode ser vazia")
	}
	raiz := NewRootCommand()
	if raiz.Version != Version() {
		t.Fatalf("comando raiz nao expoe a versao: %q", raiz.Version)
	}
	// O Cobra registra o flag no Execute; em teste e preciso pedir.
	raiz.InitDefaultVersionFlag()
	if raiz.Flags().Lookup("version") == nil {
		t.Fatal("--version precisa existir")
	}
}
