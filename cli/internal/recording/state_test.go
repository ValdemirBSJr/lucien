package recording

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStripANSI(t *testing.T) {
	input := "\x1b[32m$ docker ps\x1b[0m\r\nresultado"
	want := "$ docker ps\nresultado"
	if got := StripANSI(input); got != want {
		t.Fatalf("sanitização inesperada: %q", got)
	}
}

func TestNewSessionPreservaDescricao(t *testing.T) {
	session, err := newSession(
		"redis-cache", "Investigar latência do Redis", "", 1234, "/tmp/session.log",
	)
	if err != nil {
		t.Fatalf("criar sessão: %v", err)
	}
	if session.Description != "Investigar latência do Redis" {
		t.Fatalf("descrição não preservada: %q", session.Description)
	}
}

func TestNewSessionGeraNomeUnicoCompativelComAPI(t *testing.T) {
	provision := strings.Repeat("a", 48)
	first, err := newSession(provision, "", "", 1, "/tmp/first.log")
	if err != nil {
		t.Fatalf("criar primeira sessão: %v", err)
	}
	second, err := newSession(provision, "", "", 2, "/tmp/second.log")
	if err != nil {
		t.Fatalf("criar segunda sessão: %v", err)
	}
	if first.JobName == second.JobName {
		t.Fatal("sessões distintas não podem compartilhar o nome do Job")
	}
	if len(first.JobName) > 80 {
		t.Fatalf("nome excede o contrato da API: %d", len(first.JobName))
	}
}

func TestCappedWriterSinalizaTruncamento(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "log-*.log")
	if err != nil {
		t.Fatalf("criar log temporário: %v", err)
	}
	defer file.Close()

	writer := &cappedWriter{file: file, remaining: 4}
	if _, err := writer.Write([]byte("12345678")); err != nil {
		t.Fatalf("escrever além do limite: %v", err)
	}
	if !writer.truncated {
		t.Fatal("truncamento não foi sinalizado")
	}
	info, err := file.Stat()
	if err != nil {
		t.Fatalf("consultar log: %v", err)
	}
	if info.Size() != 4 {
		t.Fatalf("log deveria parar no limite: %d bytes", info.Size())
	}
}

func TestCappedWriterNaoSinalizaDentroDoLimite(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "log-*.log")
	if err != nil {
		t.Fatalf("criar log temporário: %v", err)
	}
	defer file.Close()

	writer := &cappedWriter{file: file, remaining: 64}
	if _, err := writer.Write([]byte("docker ps")); err != nil {
		t.Fatalf("escrever dentro do limite: %v", err)
	}
	if writer.truncated {
		t.Fatal("escrita dentro do limite não pode marcar truncamento")
	}
}

func TestPendingUploadExigeSessaoParada(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	logPath := filepath.Join(t.TempDir(), "session.log")
	if err := os.WriteFile(logPath, []byte("docker ps"), 0o600); err != nil {
		t.Fatalf("criar log: %v", err)
	}
	if err := saveSession(Session{
		PID: 123, JobName: "job-local", LogPath: logPath, Status: "RUNNING",
	}); err != nil {
		t.Fatalf("salvar sessão: %v", err)
	}

	if _, _, err := PendingUpload(); err == nil || !strings.Contains(err.Error(), "lucien stop") {
		t.Fatalf("upload deveria exigir stop: %v", err)
	}
}

func TestPendingUploadSanitizaLogPreservado(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	logPath := filepath.Join(t.TempDir(), "session.log")
	if err := os.WriteFile(logPath, []byte("\x1b[32mdocker ps\x1b[0m\r\n"), 0o600); err != nil {
		t.Fatalf("criar log: %v", err)
	}
	expected := Session{JobName: "job-local", LogPath: logPath, Status: "STOPPED"}
	if err := saveSession(expected); err != nil {
		t.Fatalf("salvar sessão: %v", err)
	}

	session, log, err := PendingUpload()
	if err != nil {
		t.Fatalf("carregar upload pendente: %v", err)
	}
	if session.JobName != expected.JobName || log != "docker ps\n" {
		t.Fatalf("sessão sanitizada inesperada: %#v %q", session, log)
	}
}

func TestStripANSIResolveBackspaceDeColagem(t *testing.T) {
	// Reproduz a colagem no terminal: o readline reexibe a linha inteira
	// precedida de um \b para cada caractere já impresso.
	comando := "last -a |head -20"
	backspaces := strings.Repeat("\b", len(comando))
	input := comando + backspaces + comando + "\r\nsaida\r\n"

	got := StripANSI(input)

	want := comando + "\nsaida\n"
	if got != want {
		t.Fatalf("backspace nao resolvido:\n got %q\nwant %q", got, want)
	}
	if strings.Contains(got, "\b") {
		t.Fatal("o log preservou caracteres de backspace")
	}
}

func TestStripANSIDescartaControlesEPreservaTabulacao(t *testing.T) {
	input := "coluna1\tcoluna2\x07\x00\x1f\x7f\nacentuação preservada\n"

	got := StripANSI(input)

	if got != "coluna1\tcoluna2\nacentuação preservada\n" {
		t.Fatalf("controles nao tratados corretamente: %q", got)
	}
}

func TestStripANSIPreservaComandoEditadoNoHistorico(t *testing.T) {
	// O readline emite \b para MOVER o cursor, nao para apagar. Tratando \b
	// como apagar, `tail -20 /caminho | grep OFF` com o cursor recuado até
	// depois de "tail " virava `tail ACK`: tudo entre eles era comido.
	base := "tail -20 /usr/local/lib/ipcmdr/data/ipcmdr1.log | grep OFF"
	recuo := strings.Repeat("\b", len("-20 /usr/local/lib/ipcmdr/data/ipcmdr1.log | grep OFF"))
	// Cursor recua ate depois de "tail ", reescreve a linha inteira e apaga
	// a cauda -- exatamente o que o readline faz ao editar.
	novo := "-20 /usr/local/lib/ipcmdr/data/ipcmdr1.log | grep ACK"

	got := StripANSI(base + recuo + novo + "\x1b[K\n")

	want := "tail -20 /usr/local/lib/ipcmdr/data/ipcmdr1.log | grep ACK\n"
	if got != want {
		t.Fatalf("comando editado foi mutilado:\n got %q\nwant %q", got, want)
	}
}

func TestStripANSINaoApagaAoMoverOCursor(t *testing.T) {
	// O caso que mutilou os runbooks de RedHat. Ao editar no meio da linha, o
	// readline emite \b puros para mover o cursor -- sem o espaco que apaga.
	// Tratando cada \b como apagar, um recuo de 40 colunas comia 40
	// caracteres do comando e o runbook publicava `tail -20 /usr/locaX`.
	base := "tail -20 /usr/local/lib/ipcmdr/data/ipcmdr1.log | grep OFF"
	entrada := base + strings.Repeat("\b", 40) + "X" + "\n"

	got := StripANSI(entrada)

	// O X sobrescreve uma coluna; o resto da linha permanece.
	want := "tail -20 /usr/locaX/lib/ipcmdr/data/ipcmdr1.log | grep OFF\n"
	if got != want {
		t.Fatalf("mover o cursor apagou texto:\n got %q\nwant %q", got, want)
	}
	if !strings.Contains(got, "ipcmdr1.log") {
		t.Fatal("o comando foi truncado ao mover o cursor")
	}
}

func TestStripANSIResolveApagamentoRealDoReadline(t *testing.T) {
	// Capturado de um readline de verdade: apagar e `\b`, espaco, `\b` por
	// caractere -- o espaco sobrescreve, os \b so movem o cursor.
	apagaUm := "\b \b"
	entrada := "grep OFF" + strings.Repeat(apagaUm, 3) + "ACK\n"

	got := StripANSI(entrada)

	if got != "grep ACK\n" {
		t.Fatalf("apagamento do readline nao resolvido: %q", got)
	}
}

func TestStripANSIPreservaFimDeLinhaPorRetornoDeCarro(t *testing.T) {
	// Equipamento de rede usa \r puro como fim de linha. Tratá-lo como
	// retorno de cursor colapsaria a saída inteira numa linha só: três linhas
	// de `show card` viravam "Slot 2 GTGOrd".
	got := StripANSI("ZTE#show card\rSlot 1 GTGH\rSlot 2 GTGO\r")

	want := "ZTE#show card\nSlot 1 GTGH\nSlot 2 GTGO\n"
	if got != want {
		t.Fatalf("fim de linha por retorno de carro foi perdido:\n got %q\nwant %q", got, want)
	}
}

func TestStripANSIPreservaCenariosDeCapturaConhecidos(t *testing.T) {
	// Guarda de não-regressão para os modos de uso que existem hoje. Cada um
	// já funcionava antes do modelo de cursor e precisa continuar idêntico:
	// equipamento de rede, máquina local, servidor RedHat e cor ANSI.
	casos := map[string]string{
		"ZTE#show ip route\r\ndefault via 10.0.0.1\r\n":  "ZTE#show ip route\ndefault via 10.0.0.1\n",
		"operador@host:~$ docker ps\r\nCONTAINER ID\r\n": "operador@host:~$ docker ps\nCONTAINER ID\n",
		"[root@rhel ~]# uname -a\r\nLinux rhel\r\n":      "[root@rhel ~]# uname -a\nLinux rhel\n",
		"\x1b[32m$ ls\x1b[0m\r\narquivo.txt\r\n":         "$ ls\narquivo.txt\n",
		"baixando  10%\rbaixando 100%\npronto\n":         "baixando  10%\nbaixando 100%\npronto\n",
	}
	for entrada, esperado := range casos {
		if got := StripANSI(entrada); got != esperado {
			t.Fatalf("cenário alterado:\n entrada %q\n got     %q\n want    %q", entrada, got, esperado)
		}
	}
}

func TestStripANSIColapsaEditorDeTelaCheia(t *testing.T) {
	// nano, vi e less entram na tela alternativa e desenham barra de menu,
	// linhas de preenchimento e reposicionamento de cursor. Antes disso o
	// bloco de saida do runbook recebia esse desenho inteiro: 652 caracteres
	// no caso do nano, 1679 no do vi, medidos em captura real de PTY.
	entrada := "operador@jump:~$ vi /etc/config\n" +
		"\x1b[?1049h\x1b[1;24r\x1b[H\x1b[2Jtimeout=10\n" +
		"~                             ~                             ~\n" +
		"\"/etc/config\" 2 lines, 31 bytes\x1b[?1049l\n" +
		"operador@jump:~$ echo depois\ndepois\n"

	got := StripANSI(entrada)

	if !strings.Contains(got, marcadorTelaCheia) {
		t.Fatalf("regiao de tela cheia nao foi colapsada: %q", got)
	}
	if strings.Contains(got, "~     ") {
		t.Fatalf("preenchimento do vi sobreviveu: %q", got)
	}
	// O comando que abriu o editor e o que veio depois precisam continuar.
	for _, esperado := range []string{
		"operador@jump:~$ vi /etc/config",
		"operador@jump:~$ echo depois",
		"depois",
	} {
		if !strings.Contains(got, esperado) {
			t.Fatalf("perdeu %q em %q", esperado, got)
		}
	}
}

func TestStripANSINaoColapsaSessaoInterativaEmTelaAlternativa(t *testing.T) {
	// tmux e screen mantem a sessao inteira na tela alternativa. Colapsar ali
	// apagaria a captura toda -- o oposto do que a regra existe para fazer.
	entrada := "\x1b[?1049h" +
		"operador@jump:~$ ls /etc\nconfig\n" +
		"operador@jump:~$ grep timeout /etc/config\ntimeout=10\n" +
		"\x1b[?1049l"

	got := StripANSI(entrada)

	if strings.Contains(got, marcadorTelaCheia) {
		t.Fatalf("colapsou uma sessao com comandos dentro: %q", got)
	}
	for _, esperado := range []string{"ls /etc", "grep timeout /etc/config", "timeout=10"} {
		if !strings.Contains(got, esperado) {
			t.Fatalf("perdeu %q em %q", esperado, got)
		}
	}
}

func TestStripANSISessaoQueTerminaDentroDoEditorNaoMuda(t *testing.T) {
	// Sem o fechamento da tela alternativa nada e colapsado: o comportamento
	// de hoje e preservado em vez de a regra comer o resto do log.
	entrada := "operador@jump:~$ vi /etc/config\n\x1b[?1049h\x1b[Htimeout=10\n"

	got := StripANSI(entrada)

	if strings.Contains(got, marcadorTelaCheia) {
		t.Fatalf("colapsou regiao sem fechamento: %q", got)
	}
	if !strings.Contains(got, "timeout=10") {
		t.Fatalf("perdeu o conteudo: %q", got)
	}
}

func TestStripANSIRemoveSequenciasDeDoisBytes(t *testing.T) {
	// `ESC(B` seleciona o conjunto de caracteres. O strip de controles C0
	// removia so o byte ESC e deixava `(B` visivel no meio do texto -- era o
	// residuo que mais aparecia na captura do nano.
	casos := map[string]string{
		"\x1b(Btimeout=10\n":  "timeout=10\n",
		"linha\x1b)0 final\n": "linha final\n",
		"antes\x1b=depois\n":  "antesdepois\n",
		"a\x1b7b\x1b8c\n":     "abc\n",
	}
	for entrada, esperado := range casos {
		if got := StripANSI(entrada); got != esperado {
			t.Fatalf("entrada %q: got %q, want %q", entrada, got, esperado)
		}
	}
}

func TestStripANSIPreservaCapturaDeSessaoSSH(t *testing.T) {
	// Guarda explicita para o cenario que mais importa: abrir SSH a partir do
	// jump e executar comandos no equipamento remoto. Nada aqui usa tela
	// alternativa, entao nada pode ser colapsado ou removido.
	entrada := "operador@jump:~$ ssh admin@10.0.0.1\r\n" +
		"admin@10.0.0.1's password: \r\n" +
		"Last login: Tue Aug 19 23:34:43 2026\r\n" +
		"[root@equipamento ~]# ip route show\r\n" +
		"default via 10.0.0.254 dev eth0\r\n" +
		"[root@equipamento ~]# exit\r\n" +
		"logout\r\nConnection to 10.0.0.1 closed.\r\n" +
		"operador@jump:~$ echo fim\r\nfim\r\n"

	got := StripANSI(entrada)

	if strings.Contains(got, marcadorTelaCheia) {
		t.Fatalf("colapsou sessao SSH: %q", got)
	}
	if got != strings.ReplaceAll(entrada, "\r\n", "\n") {
		t.Fatalf("sessao SSH alterada:\n got  %q\n want %q",
			got, strings.ReplaceAll(entrada, "\r\n", "\n"))
	}
}
