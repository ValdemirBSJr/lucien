package recording

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/lucien-runbook/lucien/internal/config"
)

const defaultMaxLogBytes int64 = 2 * 1024 * 1024

var (
	ansiCSI = regexp.MustCompile(`\x1b\[[0-?]*[ -/]*[@-~]`)
	ansiOSC = regexp.MustCompile(`\x1b\][^\x07]*(?:\x07|\x1b\\)`)
	// Apagar ate o fim da linha: ESC[K e ESC[0K.
	ansiEraseLine = regexp.MustCompile(`\x1b\[0?K`)
	// Sequencias de dois bytes que nao sao CSI nem OSC: designacao de conjunto
	// de caracteres (ESC(B), modo de teclado (ESC=, ESC>) e salvar/restaurar
	// cursor (ESC7, ESC8). Sem esta regra o strip de controles C0 removia so o
	// byte ESC e deixava `(B` visivel no meio do texto capturado.
	ansiEsc2 = regexp.MustCompile(`\x1b[()#][0-9A-Za-z]|\x1b[=>78]`)
	// Regiao da tela alternativa, onde editores e paginadores desenham. O
	// fechamento e obrigatorio: sem ele nada e colapsado, e uma sessao que
	// termine dentro do editor mantem o comportamento de hoje.
	telaAlternativa = regexp.MustCompile(`(?s)\x1b\[\?(?:1049|1047|47)h.*?\x1b\[\?(?:1049|1047|47)l`)
	// Heuristica deliberadamente frouxa de linha de prompt, usada apenas para
	// decidir se a regiao da tela alternativa pode ser colapsada.
	pareceComando  = regexp.MustCompile(`(?m)^\S*[$#>] *\S`)
	validProvision = regexp.MustCompile(`^[a-zA-Z0-9_.-]{1,48}$`)
)

// marcadorTelaCheia substitui o desenho de tela de editores e paginadores.
// Nao contem `#`, `$` nem `>`, entao nao e confundido com prompt pelo Hub.
const marcadorTelaCheia = "[sessão de tela cheia: conteúdo interativo não registrado]"

// colapsarTelaAlternativa troca o redesenho de tela por uma linha que diz o que
// houve ali.
//
// nano, vi e less entram na tela alternativa e despejam barra de menu, linhas
// de preenchimento e reposicionamento de cursor. Nada disso e par comando/saida
// -- e uma sessao interativa, que nao tem transcricao linear.
//
// A regiao so e colapsada quando nao ha nada dentro que pareca comando. Sob
// tmux ou screen a sessao inteira roda na tela alternativa: colapsar ali
// apagaria a captura toda. Na duvida, preserva o comportamento atual.
func colapsarTelaAlternativa(value string) string {
	return telaAlternativa.ReplaceAllStringFunc(value, func(regiao string) string {
		// A pergunta é sobre o texto, não sobre os bytes de controle: uma
		// sequência como ESC[>4;2m tem um `>` que a heurística leria como
		// prompt, e o vi deixava de ser colapsado por causa disso.
		texto := ansiOSC.ReplaceAllString(regiao, "")
		texto = ansiCSI.ReplaceAllString(texto, "")
		texto = ansiEsc2.ReplaceAllString(texto, "")
		if pareceComando.MatchString(texto) {
			return regiao
		}
		return "\n" + marcadorTelaCheia + "\n"
	})
}

// eraseToEndOfLine marca, dentro da linha, onde o terminal apagou o resto.
// Fica na area de uso privado do Unicode para nao colidir com conteudo.
const eraseToEndOfLine = '\uE000'

type Session struct {
	PID         int    `json:"pid"`
	Provision   string `json:"provision"`
	JobName     string `json:"job_name"`
	Description string `json:"description,omitempty"`
	// Escolhido em `lucien start -r`. Vazio significa "o dominio do autor".
	DomainFunction string `json:"domain_function,omitempty"`
	// Identidade verificavel do processo gravado. O PID sozinho e reciclado
	// pelo sistema; sem isto, `stop` pode sinalizar um processo alheio.
	ProcessIdentity string `json:"process_identity,omitempty"`
	LogPath         string `json:"log_path"`
	Status          string `json:"status"`
	LogTruncated    bool   `json:"log_truncated,omitempty"`
}

func sessionPath() (string, error) {
	directory, err := config.StateDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(directory, "session.json"), nil
}

func saveSession(session Session) error {
	path, err := sessionPath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.Marshal(session)
	if err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".session-*")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryName, path)
}

func loadSession() (Session, error) {
	path, err := sessionPath()
	if err != nil {
		return Session{}, err
	}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return Session{}, errors.New("no local session found")
	}
	if err != nil {
		return Session{}, err
	}
	var session Session
	if err := json.Unmarshal(data, &session); err != nil {
		return Session{}, fmt.Errorf("invalid session state: %w", err)
	}
	return session, nil
}

func maxLogBytes() int64 {
	value := os.Getenv("LUCIEN_MAX_LOG_BYTES")
	if value == "" {
		return defaultMaxLogBytes
	}
	parsed, err := strconv.ParseInt(value, 10, 64)
	if err != nil || parsed < 1024 || parsed > 10*1024*1024 {
		return defaultMaxLogBytes
	}
	return parsed
}

func newSession(
	provision, description, domainFunction string, pid int, logPath string,
) (Session, error) {
	// Capturada agora, enquanto o processo comprovadamente e o nosso.
	identidade, _ := processIdentity(pid)
	if !validProvision.MatchString(provision) {
		return Session{}, errors.New("provider name must use 1-48 characters: ASCII letters (no accents), numbers, dot, hyphen, or underscore -- it becomes a file name in the published repository")
	}
	randomSuffix := make([]byte, 6)
	if _, err := rand.Read(randomSuffix); err != nil {
		return Session{}, fmt.Errorf("generate session identifier: %w", err)
	}
	return Session{
		PID:             pid,
		Provision:       provision,
		JobName:         provision + "-" + time.Now().UTC().Format("20060102-150405") + "-" + hex.EncodeToString(randomSuffix),
		Description:     description,
		DomainFunction:  domainFunction,
		ProcessIdentity: identidade,
		LogPath:         logPath,
		Status:          "RUNNING",
	}, nil
}

// Stop devolve tambem se o proprio processo esta dentro do PTY gravado.
// Quem imprime o aviso de encerramento e o `lucien start`, que e dono do
// terminal real; rodando dentro da sessao, imprimir aqui tambem duplicaria
// a mensagem no mesmo terminal.
// ownsProcess confirma que o PID gravado ainda e o processo da sessao.
//
// `processExists` sozinho responde apenas "existe alguem com esse PID", o que
// e verdade tambem quando o sistema reciclou o numero -- entre `start` e
// `stop` pode ter havido um reboot. Sinalizar nessa situacao mata um processo
// alheio, e o operador nao teria como saber.
//
// Uma sessao gravada antes deste campo existir nao tem identidade para
// comparar. Nesse caso o comportamento antigo permanece: recusar deixaria a
// sessao presa, sem forma de encerra-la, e o risco de reciclagem e menor do
// que a certeza de travar o fluxo.
func ownsProcess(session Session) bool {
	if !processExists(session.PID) {
		return false
	}
	if session.ProcessIdentity == "" {
		return true
	}
	atual, ok := processIdentity(session.PID)
	return !ok || atual == session.ProcessIdentity
}

func Stop() (Session, bool, error) {
	dentro := false
	session, err := loadSession()
	if err != nil {
		return Session{}, false, err
	}
	if session.Status == "STOPPED" {
		return session, dentro, nil
	}
	if session.Status != "RUNNING" {
		return Session{}, false, errors.New("local session state is invalid")
	}
	if session.PID > 0 && ownsProcess(session) {
		dentro = insideSession(session.PID)
		if err := terminateProcess(session.PID); err != nil {
			return Session{}, false, err
		}
		deadline := time.Now().Add(5 * time.Second)
		for time.Now().Before(deadline) {
			time.Sleep(100 * time.Millisecond)
			current, loadErr := loadSession()
			if loadErr == nil && current.Status == "STOPPED" {
				session = current
				break
			}
			if !processExists(session.PID) {
				session.Status = "STOPPED"
				session.PID = 0
				_ = saveSession(session)
				break
			}
		}
		if session.Status == "RUNNING" {
			if err := killProcess(session.PID); err != nil {
				return Session{}, false, err
			}
		}
	}
	if session.Status == "RUNNING" {
		session.Status = "STOPPED"
		session.PID = 0
		if err := saveSession(session); err != nil {
			return Session{}, false, err
		}
	}
	return session, dentro, nil
}

func PendingUpload() (Session, string, error) {
	session, err := loadSession()
	if err != nil {
		return Session{}, "", err
	}
	if session.Status != "STOPPED" {
		return Session{}, "", errors.New("session is still running; run lucien stop")
	}
	data, err := os.ReadFile(session.LogPath)
	if err != nil {
		return Session{}, "", fmt.Errorf("read recorded log: %w", err)
	}
	return session, StripANSI(string(data)), nil
}

func Cleanup(session Session) error {
	path, err := sessionPath()
	if err != nil {
		return err
	}
	if err := os.Remove(session.LogPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

func StripANSI(value string) string {
	// A sequência de apagar-até-o-fim-da-linha precisa sobreviver ao strip de
	// CSI: ela carrega a informação de que o resto da linha foi apagado. Sem
	// ela, um redesenho mais curto deixaria a cauda do texto antigo para trás.
	// Antes de qualquer remocao: o colapso precisa dos marcadores ESC[?1049h/l,
	// que o strip de CSI logo abaixo apagaria.
	clean := colapsarTelaAlternativa(value)
	clean = ansiEraseLine.ReplaceAllString(clean, string(eraseToEndOfLine))
	clean = ansiOSC.ReplaceAllString(clean, "")
	clean = ansiCSI.ReplaceAllString(clean, "")
	clean = ansiEsc2.ReplaceAllString(clean, "")
	clean = strings.ReplaceAll(clean, "\r\n", "\n")
	// `\r` continua virando quebra de linha. Equipamento de rede usa `\r`
	// puro como fim de linha, e trata-lo como retorno de cursor colapsaria a
	// saida inteira numa linha so -- perda de dado pior que o problema que
	// resolveria. O redesenho do readline e tratado pelo cursor de `\b`.
	clean = strings.ReplaceAll(clean, "\r", "\n")

	lines := strings.Split(clean, "\n")
	for index, line := range lines {
		lines[index] = stripControls(renderLine(line))
	}
	return strings.Join(lines, "\n")
}

// renderLine aplica a semântica de terminal a uma linha capturada.
//
// Os dois controles que importam aqui não apagam nada por conta própria: \r
// devolve o cursor à coluna 0 e \b recua uma coluna. O que apaga é o texto
// escrito por cima.
//
// Tratar \r como quebra de linha inventava comandos que nunca foram
// executados, porque cada estado intermediário do redesenho virava uma linha
// própria. E tratar \b como apagar mutilava comando recuperado do histórico:
// o readline emite \b para navegar na linha, não para apagar, então cada
// movimento do cursor comia um caractere do comando.
//
// Com o cursor de verdade a colagem continua resolvida: os \b voltam ao
// início e a reexibição sobrescreve a cópia anterior em vez de concatená-la.
func renderLine(line string) string {
	if !strings.ContainsRune(line, '\b') &&
		!strings.ContainsRune(line, eraseToEndOfLine) {
		return line
	}
	buffer := make([]rune, 0, len(line))
	cursor := 0
	for _, symbol := range line {
		switch symbol {
		case '\b':
			if cursor > 0 {
				cursor--
			}
		case eraseToEndOfLine:
			if cursor < len(buffer) {
				buffer = buffer[:cursor]
			}
		default:
			if cursor < len(buffer) {
				buffer[cursor] = symbol
			} else {
				buffer = append(buffer, symbol)
			}
			cursor++
		}
	}
	return string(buffer)
}

// stripControls descarta os demais controles C0 e o DEL, que o PTY emite como
// sinalização de terminal e não fazem parte do comando digitado. A tabulação é
// preservada porque separa colunas em saídas legítimas.
func stripControls(line string) string {
	return strings.Map(func(symbol rune) rune {
		if symbol == '\t' {
			return symbol
		}
		if symbol < 0x20 || symbol == 0x7f {
			return -1
		}
		return symbol
	}, line)
}

type cappedWriter struct {
	file      *os.File
	remaining int64
	truncated bool
}

func (writer *cappedWriter) Write(data []byte) (int, error) {
	originalLength := len(data)
	if writer.remaining <= 0 {
		if originalLength > 0 {
			writer.truncated = true
		}
		return originalLength, nil
	}
	if int64(len(data)) > writer.remaining {
		data = data[:writer.remaining]
		writer.truncated = true
	}
	written, err := writer.file.Write(data)
	writer.remaining -= int64(written)
	if err != nil {
		return written, err
	}
	// O stdout continua fluindo mesmo quando o limite persistido foi alcançado.
	return originalLength, nil
}
