//go:build !windows

package recording

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// processIdentity devolve um identificador que distingue o processo de outro
// que venha a reutilizar o mesmo PID.
//
// O PID sozinho não identifica nada de forma durável: o sistema os recicla, e
// entre `lucien start` e `lucien stop` pode haver um reboot. Sinalizar por PID
// nu significa que, na hora errada, o Lucien mata um processo alheio.
//
// O par grupo + instante de início resolve: o instante é medido desde o boot e
// não se repete para o mesmo PID dentro de uma mesma inicialização, e um PID
// reciclado quase sempre cai em outro grupo. Guardar o par na sessão permite
// comprovar a posse antes de qualquer sinal.
func processIdentity(pid int) (string, bool) {
	if pid <= 0 {
		return "", false
	}
	dados, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return "", false
	}
	// O campo 2 é o nome do executável entre parênteses e pode conter espaços,
	// então a leitura dos numéricos começa depois do último ')'.
	fim := strings.LastIndexByte(string(dados), ')')
	if fim == -1 {
		return "", false
	}
	campos := strings.Fields(string(dados)[fim+1:])
	// Depois do ')' o primeiro campo é o estado, que é o campo 3 do formato.
	// Grupo é o campo 5 e o instante de início é o 22.
	const indiceGrupo = 2
	const indiceInicio = 19
	if len(campos) <= indiceInicio {
		return "", false
	}
	grupo := campos[indiceGrupo]
	inicio := campos[indiceInicio]
	if _, err := strconv.ParseInt(grupo, 10, 64); err != nil {
		return "", false
	}
	if _, err := strconv.ParseUint(inicio, 10, 64); err != nil {
		return "", false
	}
	return fmt.Sprintf("%s:%s", grupo, inicio), true
}
