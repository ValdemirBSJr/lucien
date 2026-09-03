package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"

	"github.com/lucien-runbook/lucien/internal/config"
)

// LocalDraft é o que o editor do desktop preserva localmente entre uma
// tentativa de publicação e a próxima -- texto e imagens anexadas, exatamente
// o que se perde hoje quando a publicação falha e o operador sai do editor
// (ou o app fecha) antes de corrigir e tentar de novo.
type LocalDraft struct {
	Markdown string        `json:"markdown"`
	Assets   []EditorAsset `json:"assets"`
}

// localDraftPath espelha internal/draft/store.go (mesmo diretório de estado,
// mesma ideia de nome opaco por hash), mas com anexos -- por isso um arquivo
// próprio do desktop, em vez de estender o pacote que o CLI de terminal usa
// para texto puro.
func localDraftPath(jobID string) (string, error) {
	state, err := config.StateDir()
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256([]byte("desktop-draft:" + jobID))
	return filepath.Join(state, "drafts", hex.EncodeToString(digest[:])+".json"), nil
}

// SaveLocalDraft grava texto e imagens no disco -- chamado a cada tentativa
// de publicação, bem-sucedida ou não, para que reabrir o mesmo runbook (pela
// tabela) sempre retome o que havia de mais recente.
func (a *App) SaveLocalDraft(jobID string, draft LocalDraft) error {
	path, err := localDraftPath(jobID)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	encoded, err := json.Marshal(draft)
	if err != nil {
		return err
	}
	return escreverAtomico(path, encoded)
}

// escreverAtomico grava por arquivo temporário e rename, com 0600.
//
// O rename é o que evita meio arquivo no disco: uma queda no meio da escrita
// deixa o temporário para trás (removido pelo defer) e preserva a versão
// anterior intacta, em vez de truncar a boa. Vale para o rascunho de um job
// e para o runbook ainda local -- os dois guardam trabalho que não está em
// nenhum outro lugar.
func escreverAtomico(path string, conteudo []byte) error {
	temporary, err := os.CreateTemp(filepath.Dir(path), ".draft-*")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(conteudo); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryName, path)
}

// LoadLocalDraft devolve LocalDraft{} sem erro quando não há nada salvo --
// "nunca editado localmente" não é uma falha, é o estado inicial normal de
// um runbook que só existe no Hub.
func (a *App) LoadLocalDraft(jobID string) (LocalDraft, error) {
	path, err := localDraftPath(jobID)
	if err != nil {
		return LocalDraft{}, err
	}
	content, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return LocalDraft{}, nil
	}
	if err != nil {
		return LocalDraft{}, err
	}
	var draft LocalDraft
	if err := json.Unmarshal(content, &draft); err != nil {
		return LocalDraft{}, err
	}
	return draft, nil
}

// DeleteLocalDraft remove o rascunho salvo -- chamado depois de uma
// publicação bem-sucedida, já que o Hub passa a ser a fonte de verdade.
func (a *App) DeleteLocalDraft(jobID string) error {
	path, err := localDraftPath(jobID)
	if err != nil {
		return err
	}
	err = os.Remove(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}
