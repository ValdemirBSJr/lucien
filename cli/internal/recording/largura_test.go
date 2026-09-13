package recording

import (
	"strings"
	"testing"
)

// promptLongo devolve um prompt de `visivel` colunas, com as cores que o bash
// emite, e o mesmo prompt como ele fica depois de limpo.
func promptLongo(visivel int) (bruto string, limpo string) {
	usuario := "operador@jump"
	caminho := "/srv/" + strings.Repeat("d", visivel-len(usuario)-len(":/srv/")-len("$ "))
	bruto = "\x1b[01;32m" + usuario + "\x1b[00m:\x1b[01;34m" + caminho + "\x1b[00m$ "
	limpo = usuario + ":" + caminho + "$ "
	return bruto, limpo
}

// O que o readline emite ao trazer `du -sh /var/log` do histórico, ir ao
// início com Home e digitar `sudo `, quando o prompt passa da largura da
// janela: \r volta ao início da linha FÍSICA e ESC[C anda até o comando.
// Capturado de um PTY real de 80 colunas com um prompt de 84.
const insereSudoNaLinhaQuebrada = "\r\x1b[C\x1b[C\x1b[C\x1b[C" +
	"\x1b[1@s\x1b[1@u\x1b[C\x1b[1@d\b\x1b[1@o\x1b[1@ "

func TestStripANSIAplicaEdicaoEmPromptMaisLargoQueAJanela(t *testing.T) {
	bruto, limpo := promptLongo(84)
	entrada := string(marcadorLargura(80)) + bruto + "du -sh /var/log" +
		insereSudoNaLinhaQuebrada + "\r\n682M\t/var/log\r\n"

	got := StripANSI(entrada)

	want := limpo + "sudo du -sh /var/log\n682M\t/var/log\n"
	if got != want {
		t.Fatalf("edição na linha quebrada:\n got %q\nwant %q", got, want)
	}
}

func TestStripANSISegueALarguraDepoisDeRedimensionar(t *testing.T) {
	// A janela cresce de 80 para 120 no meio da sessão. Com a largura antiga,
	// o \r voltaria para a coluna 80 e o `sudo` cairia dentro do prompt.
	bruto, limpo := promptLongo(124)
	entrada := string(marcadorLargura(80)) + "operador@jump:~$ echo antes\r\nantes\r\n" +
		string(marcadorLargura(120)) + bruto + "du -sh /var/log" +
		insereSudoNaLinhaQuebrada + "\r\n682M\t/var/log\r\n"

	got := StripANSI(entrada)

	if !strings.Contains(got, "\n"+limpo+"sudo du -sh /var/log\n") {
		t.Fatalf("a largura nova não foi aplicada:\n%q", got)
	}
}

func TestStripANSISemLarguraMantemOComportamentoAnterior(t *testing.T) {
	// Log gravado por um CLI anterior, ou já limpo pelo `session edit`: sem
	// a largura não há como achar o início da linha física, e o \r segue
	// fechando a linha como antes. O comando original não é mutilado.
	bruto, limpo := promptLongo(84)
	entrada := bruto + "du -sh /var/log" + insereSudoNaLinhaQuebrada + "\r\n"

	got := StripANSI(entrada)

	if !strings.HasPrefix(got, limpo+"du -sh /var/log\n") {
		t.Fatalf("sem largura, a linha original mudou:\n%q", got)
	}
}

func TestStripANSINaoMostraOMarcadorDeLargura(t *testing.T) {
	casos := map[string]string{
		string(marcadorLargura(80)) + "linha\n":              "linha\n",
		"ab" + string(marcadorLargura(132)) + "cd\n":         "abcd\n",
		"ab\b" + string(marcadorLargura(132)) + "X\n":        "aX\n",
		string(marcadorLargura(80)) + "\x1b[31mcor\x1b[0m\n": "cor\n",
	}
	for entrada, esperado := range casos {
		if got := StripANSI(entrada); got != esperado {
			t.Fatalf("marcador de largura vazou:\n entrada %q\n got     %q\n want    %q", entrada, got, esperado)
		}
	}
}

func TestStripANSIComLarguraPreservaFimDeLinhaPorRetorno(t *testing.T) {
	// Só \r seguido de movimento de cursor é retorno dentro da linha. Fim de
	// linha de equipamento e barra de progresso continuam como antes.
	casos := map[string]string{
		"ZTE#show card\rSlot 1 GTGH\rSlot 2 GTGO\r":    "ZTE#show card\nSlot 1 GTGH\nSlot 2 GTGO\n",
		"baixando  10%\r\x1b[Kbaixando 100%\npronto\n": "baixando  10%\nbaixando 100%\npronto\n",
		"baixando  10%\rbaixando 100%\npronto\n":       "baixando  10%\nbaixando 100%\npronto\n",
	}
	for entrada, esperado := range casos {
		got := StripANSI(string(marcadorLargura(80)) + entrada)
		if got != esperado {
			t.Fatalf("fim de linha alterado com largura:\n entrada %q\n got     %q\n want    %q", entrada, got, esperado)
		}
	}
}

func TestStripANSIColapsoDoEditorGuardaALargura(t *testing.T) {
	// A janela foi redimensionada com o nano aberto. A região do nano vira
	// uma linha, mas a largura nova tem de continuar valendo depois dela.
	bruto, limpo := promptLongo(124)
	entrada := string(marcadorLargura(80)) + "operador@jump:~$ nano notas\r\n" +
		"\x1b[?1049h\x1b[H  GNU nano 7.2" + string(marcadorLargura(120)) + "\x1b[?1049l" +
		bruto + "du -sh /var/log" + insereSudoNaLinhaQuebrada + "\r\n"

	got := StripANSI(entrada)

	if !strings.Contains(got, marcadorTelaCheia) {
		t.Fatalf("o editor não foi colapsado:\n%q", got)
	}
	if !strings.Contains(got, limpo+"sudo du -sh /var/log\n") {
		t.Fatalf("a largura se perdeu no colapso:\n%q", got)
	}
}

// Capturado de um PTY real de 80 colunas: prompt de 21, e o `cd` digitado
// atravessa a margem. Na coluna 80 o readline imprime um espaço, que força a
// quebra, e volta com \r ao início da linha física nova.
const (
	promptCurto      = "\x1b[01;32mlucien@lucien-lab\x1b[00m:\x1b[01;34m~\x1b[00m$ "
	cdAteAMargem     = "cd /tmp/diretorio-de-procedimentos-longo/diretorio-de-proce"
	cdDepoisDaMargem = "dimentos-longo/diretorio-de-procedimentos-longo"
)

func TestStripANSINaoCortaComandoMaisLongoQueAJanela(t *testing.T) {
	// Antes, o resto do comando virava uma linha de saída e o Hub publicava
	// `cd /tmp/diretorio-de-procedimentos-longo/diretorio-de-proce`.
	entrada := string(marcadorLargura(80)) + promptCurto + cdAteAMargem + " \r" +
		cdDepoisDaMargem + "\r\n"

	got := StripANSI(entrada)

	want := "lucien@lucien-lab:~$ " + cdAteAMargem + cdDepoisDaMargem + "\n"
	if got != want {
		t.Fatalf("comando cortado na margem:\n got %q\nwant %q", got, want)
	}
}

func TestStripANSIComandoLongoSemLarguraMantemOComportamentoAnterior(t *testing.T) {
	entrada := promptCurto + cdAteAMargem + " \r" + cdDepoisDaMargem + "\r\n"

	got := StripANSI(entrada)

	want := "lucien@lucien-lab:~$ " + cdAteAMargem + " \n" + cdDepoisDaMargem + "\n"
	if got != want {
		t.Fatalf("sem largura, o comportamento mudou:\n got %q\nwant %q", got, want)
	}
}

func TestStripANSIEspacoAntesDoRetornoForaDaMargemFechaALinha(t *testing.T) {
	// Equipamento que termina linha com \r e deixa um espaço antes dele: fora
	// da primeira coluna de uma linha física, o \r continua fim de linha.
	entrada := string(marcadorLargura(80)) + "ZTE#show card \rSlot 1 GTGH \rSlot 2 GTGO\r"

	got := StripANSI(entrada)

	want := "ZTE#show card \nSlot 1 GTGH \nSlot 2 GTGO\n"
	if got != want {
		t.Fatalf("fim de linha de equipamento alterado:\n got %q\nwant %q", got, want)
	}
}

func TestMarcadorDeLarguraLimitaOValor(t *testing.T) {
	// Largura absurda não pode virar um salto de milhares de colunas.
	if got := StripANSI(string(marcadorLargura(0)) + "a\n"); got != "a\n" {
		t.Fatalf("largura zero: %q", got)
	}
	if got := StripANSI(string(marcadorLargura(99999)) + "a\n"); got != "a\n" {
		t.Fatalf("largura enorme: %q", got)
	}
}
