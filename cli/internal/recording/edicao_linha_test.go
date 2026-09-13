package recording

import (
	"strings"
	"testing"
)

// Prompt exatamente como o bash o emite, com as cores.
const promptColorido = "\x1b[01;32mlucien@lucien-lab\x1b[00m:\x1b[01;34m~\x1b[00m$ "

func TestStripANSIAplicaInsercaoDoReadlineAoEditarHistorico(t *testing.T) {
	// Capturado de um PTY real: seta para cima traz `du -sh /var/log`, Home
	// (ou Ctrl-A) volta ao início e `sudo ` é digitado. O readline recua com
	// \b e insere cada letra com ESC[1@. Sem aplicar a inserção, o runbook
	// publicava `suo sh /var/log`.
	entrada := promptColorido + "du -sh /var/log" + strings.Repeat("\b", 15) +
		"\x1b[1@s\x1b[1@u\x1b[C\x1b[1@d\b\x1b[1@o\x1b[1@ " +
		"\r\n682M\t/var/log\r\n"

	got := StripANSI(entrada)

	want := "lucien@lucien-lab:~$ sudo du -sh /var/log\n682M\t/var/log\n"
	if got != want {
		t.Fatalf("edição do histórico mutilada:\n got %q\nwant %q", got, want)
	}
}

func TestStripANSIAplicaApagarDaTeclaDelete(t *testing.T) {
	// Capturado de um PTY real: `du -sh /varr/log`, quatro setas para a
	// esquerda e Delete, que apaga o `/` sob o cursor. O readline emite
	// ESC[1P e redesenha a cauda; ignorando o ESC[1P, a cauda sobrescrevia o
	// texto e duplicava o último caractere.
	entrada := "du -sh /varr/log" + strings.Repeat("\b", 4) + "\x1b[1Plog\b\b\b\r\n"

	got := StripANSI(entrada)

	if got != "du -sh /varrlog\n" {
		t.Fatalf("Delete não aplicado: %q", got)
	}
}

func TestStripANSIAplicaContagemDasSequenciasDeEdicao(t *testing.T) {
	casos := map[string]string{
		// recua três, apaga dois
		"abcdef\x1b[3D\x1b[2P\n": "abcf\n",
		// recua dois, avança dois e escreve
		"abc\x1b[2D\x1b[2CX\n": "abcX\n",
		// abre duas colunas no meio
		"ad\x1b[1D\x1b[2@bc\n": "abcd\n",
		// sem número e com zero valem um
		"ab\x1b[D\x1b[0PX\n": "aX\n",
		// avançar além do texto deixa colunas em branco
		"a\x1b[3Cb\n": "a   b\n",
		// inserir ou apagar além do texto não faz nada
		"ab\x1b[5C\x1b[2@\x1b[2P\n": "ab\n",
	}
	for entrada, esperado := range casos {
		if got := StripANSI(entrada); got != esperado {
			t.Fatalf("sequência de edição:\n entrada %q\n got     %q\n want    %q", entrada, got, esperado)
		}
	}
}

func TestStripANSILimitaContagemDeEdicao(t *testing.T) {
	// Um ESC[99999C na saída de um programa não pode inflar o log.
	got := StripANSI("a\x1b[99999Cb\n")

	if len(got) > limiteEdicaoLinha+4 {
		t.Fatalf("contagem sem teto: %d bytes", len(got))
	}
}

func TestStripANSILinhaSemEdicaoNaoMuda(t *testing.T) {
	// Cursor que só se move por cor ou negrito continua fora do modelo.
	entrada := "\x1b[1mnegrito\x1b[0m e \x1b[31mvermelho\x1b[0m\n"

	if got := StripANSI(entrada); got != "negrito e vermelho\n" {
		t.Fatalf("linha sem edição alterada: %q", got)
	}
}
