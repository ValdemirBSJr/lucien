//go:build !windows

package recording

import (
	"os"
	"strconv"
	"strings"
)

// insideSession informa se este processo descende do `lucien start` da sessão.
//
// Serve para decidir quem imprime o aviso de encerramento. Rodando `lucien
// stop` dentro do PTY gravado, os dois processos escrevem no mesmo terminal e
// a mensagem sai duplicada; de outro terminal, cada um escreve no seu e as
// duas são desejáveis.
//
// A dúvida é resolvida subindo a cadeia de pais em /proc. O limite de saltos
// existe porque um /proc inconsistente -- contêiner, PID reciclado -- não pode
// travar o encerramento da sessão: na dúvida devolvemos false, que no pior
// caso repete a mensagem em vez de engoli-la.
func insideSession(pid int) bool {
	if pid <= 0 {
		return false
	}
	current := os.Getpid()
	for salto := 0; salto < 64 && current > 1; salto++ {
		if current == pid {
			return true
		}
		parent, ok := parentPID(current)
		if !ok {
			return false
		}
		current = parent
	}
	return false
}

func parentPID(pid int) (int, bool) {
	dados, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return 0, false
	}
	// O campo 2 é o nome do executável entre parênteses e pode conter espaços,
	// então a leitura começa depois do último ')'.
	fim := strings.LastIndexByte(string(dados), ')')
	if fim == -1 {
		return 0, false
	}
	campos := strings.Fields(string(dados)[fim+1:])
	// Depois do ')' vêm o estado e o PPid.
	if len(campos) < 2 {
		return 0, false
	}
	parent, err := strconv.Atoi(campos[1])
	if err != nil {
		return 0, false
	}
	return parent, true
}
