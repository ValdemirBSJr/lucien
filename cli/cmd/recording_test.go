package cmd

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/lucien-runbook/lucien/internal/api"
	"github.com/lucien-runbook/lucien/internal/recording"
	"github.com/spf13/cobra"
)

type fakeSessionUploader struct {
	uploadJob          api.Job
	uploadErr          error
	getJob             api.Job
	getErr             error
	getCalls           int
	skipEnrichmentSeen bool
	domainFunctionSeen string
}

func (fake *fakeSessionUploader) Upload(
	_ context.Context, _ string, _ string, _ string, skipEnrichment bool,
	domainFunction string,
) (api.Job, error) {
	fake.skipEnrichmentSeen = skipEnrichment
	fake.domainFunctionSeen = domainFunction
	return fake.uploadJob, fake.uploadErr
}

func (fake *fakeSessionUploader) GetJob(context.Context, string) (api.Job, error) {
	fake.getCalls++
	return fake.getJob, fake.getErr
}

func TestStopEncerraSemDependerDeAutenticacao(t *testing.T) {
	called := false
	command := buildStopCommand(func() (recording.Session, bool, error) {
		called = true
		return recording.Session{JobName: "job-local"}, false, nil
	})
	output := &bytes.Buffer{}
	command.SetOut(output)

	if err := command.Execute(); err != nil {
		t.Fatalf("executar stop: %v", err)
	}
	if !called {
		t.Fatal("stop local não foi executado")
	}
	for _, expected := range []string{
		"Next: lucien upload",
		"upload will return the Job_ID and status command",
	} {
		if !strings.Contains(output.String(), expected) {
			t.Fatalf("orientação pós-stop ausente (%q): %q", expected, output.String())
		}
	}
	if strings.Contains(output.String(), "Job_ID:") {
		t.Fatalf("stop não pode inventar Job_ID antes do aceite: %q", output.String())
	}
}

func TestStopAvisaQuandoLogFoiTruncado(t *testing.T) {
	command := buildStopCommand(func() (recording.Session, bool, error) {
		return recording.Session{JobName: "job-local", LogTruncated: true}, false, nil
	})
	output := &bytes.Buffer{}
	warnings := &bytes.Buffer{}
	command.SetOut(output)
	command.SetErr(warnings)

	if err := command.Execute(); err != nil {
		t.Fatalf("executar stop: %v", err)
	}
	if !strings.Contains(warnings.String(), "truncated") {
		t.Fatalf("aviso de truncamento ausente: %q", warnings.String())
	}
}

func TestUploadPreservaSessaoQuandoAutenticacaoFalha(t *testing.T) {
	cleaned := false
	command := buildUploadCommand(
		func() (recording.Session, string, error) {
			return recording.Session{JobName: "job-local"}, "docker ps", nil
		},
		func() (sessionUploader, error) {
			return nil, errors.New("nenhum usuário configurado")
		},
		func(recording.Session) error {
			cleaned = true
			return nil
		},
	)

	err := command.Execute()
	if err == nil || !strings.Contains(err.Error(), "session preserved locally") {
		t.Fatalf("erro deveria confirmar preservação local: %v", err)
	}
	if cleaned {
		t.Fatal("sessão não pode ser removida após falha de autenticação")
	}
}

func TestUploadReconciliaRespostaPerdidaEExecutaCleanup(t *testing.T) {
	fake := &fakeSessionUploader{
		uploadErr: errors.New("timeout"),
		getJob:    api.Job{ID: "job-id", Status: "PROCESSING"},
	}
	cleaned := false
	command := buildUploadCommand(
		func() (recording.Session, string, error) {
			return recording.Session{JobName: "job-local"}, "docker ps", nil
		},
		func() (sessionUploader, error) { return fake, nil },
		func(recording.Session) error {
			cleaned = true
			return nil
		},
	)
	output := &bytes.Buffer{}
	command.SetOut(output)

	if err := command.Execute(); err != nil {
		t.Fatalf("reconciliar upload: %v", err)
	}
	if fake.getCalls != 1 || !cleaned {
		t.Fatalf("reconciliação incompleta: get=%d cleanup=%v", fake.getCalls, cleaned)
	}
	if !strings.Contains(output.String(), "Job_ID: job-id") {
		t.Fatalf("Job_ID ausente: %q", output.String())
	}
	if !strings.Contains(output.String(), "Status: PROCESSING") ||
		!strings.Contains(output.String(), "lucien job status job-id") {
		t.Fatalf("orientação assíncrona ausente: %q", output.String())
	}
}

func TestUploadEnviaSkipEnrichmentSomenteComOFlag(t *testing.T) {
	for _, caso := range []struct {
		nome     string
		args     []string
		esperado bool
	}{
		{nome: "sem flag", args: nil, esperado: false},
		{nome: "flag longo", args: []string{"--skip-enrichment"}, esperado: true},
		{nome: "flag curto", args: []string{"-s"}, esperado: true},
	} {
		t.Run(caso.nome, func(t *testing.T) {
			fake := &fakeSessionUploader{uploadJob: api.Job{ID: "job-id", Status: "PROCESSING"}}
			command := buildUploadCommand(
				func() (recording.Session, string, error) {
					return recording.Session{JobName: "job-local"}, "docker ps", nil
				},
				func() (sessionUploader, error) { return fake, nil },
				func(recording.Session) error { return nil },
			)
			command.SetOut(&bytes.Buffer{})
			command.SetArgs(caso.args)
			if err := command.Execute(); err != nil {
				t.Fatalf("upload: %v", err)
			}
			if fake.skipEnrichmentSeen != caso.esperado {
				t.Fatalf("skip_enrichment = %v, esperado %v", fake.skipEnrichmentSeen, caso.esperado)
			}
		})
	}
}

func TestStartRecusaRoleForaDaGramatica(t *testing.T) {
	// A lista valida vive no Hub; aqui so o erro de digitacao obvio, antes de
	// gravar uma sessao inteira para o upload recusar depois.
	for _, invalido := range []string{"Acessos", "ac", "acessos!", "1acessos"} {
		command := newStartCommand()
		command.SetArgs([]string{"equipamento", "-r", invalido})
		command.SetOut(&bytes.Buffer{})
		command.SetErr(&bytes.Buffer{})
		if err := command.Execute(); err == nil {
			t.Fatalf("role invalida foi aceita: %q", invalido)
		}
	}
}

func TestUploadEnviaRoleGravadaNaSessao(t *testing.T) {
	fake := &fakeSessionUploader{uploadJob: api.Job{ID: "job-id", Status: "PENDING"}}
	session := recording.Session{
		JobName:        "equipamento-20260819-120000-abc123",
		DomainFunction: "acessos",
	}
	command := buildUploadCommand(
		func() (recording.Session, string, error) { return session, "docker ps\n", nil },
		func() (sessionUploader, error) { return fake, nil },
		func(recording.Session) error { return nil },
	)
	command.SetOut(&bytes.Buffer{})
	command.SetErr(&bytes.Buffer{})

	if err := command.Execute(); err != nil {
		t.Fatalf("executar upload: %v", err)
	}
	if fake.domainFunctionSeen != "acessos" {
		t.Fatalf("role não chegou ao Hub: %q", fake.domainFunctionSeen)
	}
}

func TestStopEStartProduzemAMesmaMensagem(t *testing.T) {
	// A mensagem precisa sair pelas duas portas. `lucien stop` roda dentro do
	// PTY gravado e mata o processo que copia a saida para a tela, entao a
	// impressao dele corre contra a morte do proprio leitor -- em terminais
	// intermediados ela se perdia. Quem garante a entrega e o `start`, dono do terminal
	// real, que imprime depois de restaurar o terminal.
	sessao := recording.Session{JobName: "olt-rota-down-20260819-233443-2f7add630842"}

	viaStop := &bytes.Buffer{}
	comando := buildStopCommand(func() (recording.Session, bool, error) { return sessao, false, nil })
	comando.SetOut(viaStop)
	comando.SetErr(&bytes.Buffer{})
	if err := comando.Execute(); err != nil {
		t.Fatalf("executar stop: %v", err)
	}

	viaStart := &bytes.Buffer{}
	auxiliar := &cobra.Command{}
	auxiliar.SetOut(viaStart)
	printSessionStopped(auxiliar, sessao)

	if viaStop.String() != viaStart.String() {
		t.Fatalf("mensagens divergiram:\nstop:  %q\nstart: %q", viaStop, viaStart)
	}
	if !strings.Contains(viaStart.String(), "Next: lucien upload") {
		t.Fatalf("mensagem perdeu o proximo passo: %q", viaStart)
	}
	if !strings.Contains(viaStart.String(), sessao.JobName) {
		t.Fatalf("mensagem nao identifica a sessao: %q", viaStart)
	}
}

func TestStopNaoDuplicaMensagemDentroDaSessao(t *testing.T) {
	// Rodando dentro do PTY gravado, quem imprime e o processo do `start`, no
	// mesmo terminal. O `stop` precisa se calar, senao o operador ve o aviso
	// duas vezes -- foi o que aconteceu depois que o `start` passou a imprimir.
	sessao := recording.Session{JobName: "consultas-dhcp-20260820-191205-8165508c6556"}

	saida := &bytes.Buffer{}
	comando := buildStopCommand(func() (recording.Session, bool, error) {
		return sessao, true, nil
	})
	comando.SetOut(saida)
	comando.SetErr(&bytes.Buffer{})

	if err := comando.Execute(); err != nil {
		t.Fatalf("executar stop: %v", err)
	}
	if saida.Len() != 0 {
		t.Fatalf("stop imprimiu dentro da sessao: %q", saida)
	}
}

func TestStopImprimeDeOutroTerminal(t *testing.T) {
	// De outro terminal, cada processo escreve no seu: as duas mensagens sao
	// desejaveis, e calar o stop deixaria aquele terminal sem resposta.
	sessao := recording.Session{JobName: "consultas-dhcp-20260820-191205-8165508c6556"}

	saida := &bytes.Buffer{}
	comando := buildStopCommand(func() (recording.Session, bool, error) {
		return sessao, false, nil
	})
	comando.SetOut(saida)
	comando.SetErr(&bytes.Buffer{})

	if err := comando.Execute(); err != nil {
		t.Fatalf("executar stop: %v", err)
	}
	if !strings.Contains(saida.String(), "Next: lucien upload") {
		t.Fatalf("stop ficou mudo fora da sessao: %q", saida)
	}
}

func TestAvisoDeTruncamentoAcompanhaOEncerramento(t *testing.T) {
	// O aviso saiu do `stop` para a funcao compartilhada: se ficasse la, o
	// operador que encerra dentro da sessao nunca saberia que o log foi
	// truncado, porque o stop se cala nesse caso.
	erro := &bytes.Buffer{}
	auxiliar := &cobra.Command{}
	auxiliar.SetOut(&bytes.Buffer{})
	auxiliar.SetErr(erro)

	printSessionStopped(auxiliar, recording.Session{
		JobName:      "sessao-truncada",
		LogTruncated: true,
	})

	if !strings.Contains(erro.String(), "truncated") {
		t.Fatalf("aviso de truncamento nao acompanhou o encerramento: %q", erro)
	}
}
