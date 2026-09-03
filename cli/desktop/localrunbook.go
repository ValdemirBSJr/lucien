package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"

	"github.com/lucien-runbook/lucien/internal/config"
)

// LocalRunbook é um runbook que ainda NÃO existe no Hub: composto no editor,
// guardado em disco, e enviado só quando o operador publicar.
//
// É o irmão anterior do LocalDraft. Aquele preserva o texto de um job que já
// nasceu lá (por isso é indexado pelo job_id) e existe para uma publicação que
// falhou no meio não perder o trabalho. Este cobre o estágio antes disso, em
// que não há job nenhum -- o que até aqui vivia apenas numa store em memória
// do frontend e se perdia ao fechar o app ou ao voltar para a lista.
//
// Guarda o RawLog junto do Markdown de propósito: o rascunho montado perdeu a
// forma original, e é dela que a SLM do Hub extrai comando, impacto e
// rollback. Sem isso, retomar um rascunho salvo deixaria o "Enriquecer" sem
// entrada, exatamente o caso que já nos mordeu antes.
type LocalRunbook struct {
	ID             string        `json:"id"`
	Name           string        `json:"name"`
	Description    string        `json:"description"`
	DomainFunction string        `json:"domainFunction"`
	RawLog         string        `json:"rawLog"`
	Markdown       string        `json:"markdown"`
	Assets         []EditorAsset `json:"assets"`
	CreatedAt      string        `json:"createdAt"`
}

// O id é gerado aqui e nunca vem do frontend, porque ele vira nome de arquivo.
// A checagem impede que um valor inesperado escape do diretório de estado.
var localRunbookID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// novoIDLocal produz um UUIDv4. O formato não é exigência do Hub -- este id
// nunca chega lá --, mas mantém a mesma forma que o resto do projeto usa para
// identificar runbook, e é o que `localRunbookID` valida antes de virar
// caminho de arquivo.
func novoIDLocal() (string, error) {
	valor := make([]byte, 16)
	if _, err := rand.Read(valor); err != nil {
		return "", fmt.Errorf("generate local runbook id: %w", err)
	}
	valor[6] = (valor[6] & 0x0f) | 0x40
	valor[8] = (valor[8] & 0x3f) | 0x80
	texto := hex.EncodeToString(valor)
	return texto[0:8] + "-" + texto[8:12] + "-" + texto[12:16] + "-" +
		texto[16:20] + "-" + texto[20:32], nil
}

func localRunbookDir() (string, error) {
	state, err := config.StateDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(state, "local-runbooks"), nil
}

func localRunbookPath(id string) (string, error) {
	if !localRunbookID.MatchString(id) {
		return "", errors.New("invalid local runbook id")
	}
	dir, err := localRunbookDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, id+".json"), nil
}

// SaveLocalRunbook grava e devolve o registro, já com id e data de criação
// quando é a primeira gravação. Devolver o registro inteiro (em vez de só o
// id) deixa o frontend guardar um objeto só, sem remontar o que mandou.
func (a *App) SaveLocalRunbook(runbook LocalRunbook) (LocalRunbook, error) {
	if runbook.ID == "" {
		gerado, err := novoIDLocal()
		if err != nil {
			return LocalRunbook{}, err
		}
		runbook.ID = gerado
	}
	if runbook.CreatedAt == "" {
		runbook.CreatedAt = time.Now().UTC().Format(time.RFC3339)
	}
	path, err := localRunbookPath(runbook.ID)
	if err != nil {
		return LocalRunbook{}, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return LocalRunbook{}, err
	}
	encoded, err := json.Marshal(runbook)
	if err != nil {
		return LocalRunbook{}, err
	}
	if err := escreverAtomico(path, encoded); err != nil {
		return LocalRunbook{}, err
	}
	return runbook, nil
}

// LoadLocalRunbook levanta quando não há nada salvo -- diferente do
// LoadLocalDraft, onde "nunca editado" é estado inicial normal. Aqui o
// registro É o runbook: pedir um que não existe é erro de quem pediu.
func (a *App) LoadLocalRunbook(id string) (LocalRunbook, error) {
	path, err := localRunbookPath(id)
	if err != nil {
		return LocalRunbook{}, err
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return LocalRunbook{}, err
	}
	var runbook LocalRunbook
	if err := json.Unmarshal(content, &runbook); err != nil {
		return LocalRunbook{}, err
	}
	return runbook, nil
}

// ListLocalRunbooks devolve os rascunhos do mais novo para o mais antigo.
//
// Um arquivo ilegível é pulado, não derruba a listagem: um rascunho corrompido
// não pode esconder todos os outros da tela -- e o operador ainda tem o botão
// de apagar para se livrar dele.
func (a *App) ListLocalRunbooks() ([]LocalRunbook, error) {
	dir, err := localRunbookDir()
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(dir)
	if errors.Is(err, os.ErrNotExist) {
		return []LocalRunbook{}, nil
	}
	if err != nil {
		return nil, err
	}
	runbooks := make([]LocalRunbook, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		content, err := os.ReadFile(filepath.Join(dir, entry.Name()))
		if err != nil {
			continue
		}
		var runbook LocalRunbook
		if err := json.Unmarshal(content, &runbook); err != nil {
			continue
		}
		if runbook.ID == "" {
			continue
		}
		runbooks = append(runbooks, runbook)
	}
	sort.Slice(runbooks, func(i, j int) bool {
		if runbooks[i].CreatedAt == runbooks[j].CreatedAt {
			return runbooks[i].ID > runbooks[j].ID
		}
		return runbooks[i].CreatedAt > runbooks[j].CreatedAt
	})
	return runbooks, nil
}

// DeleteLocalRunbook some com o rascunho. Apagar o que já não existe não é
// falha: é o estado que o chamador queria.
func (a *App) DeleteLocalRunbook(id string) error {
	path, err := localRunbookPath(id)
	if err != nil {
		return err
	}
	err = os.Remove(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}
