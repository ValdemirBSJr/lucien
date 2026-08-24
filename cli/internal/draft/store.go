package draft

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/lucien-runbook/lucien/internal/config"
)

func pathFor(identifier string) (string, error) {
	state, err := config.StateDir()
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256([]byte(identifier))
	return filepath.Join(state, "drafts", hex.EncodeToString(digest[:])+".md"), nil
}

func Save(identifier string, content []byte) error {
	path, err := pathFor(identifier)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
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
	if _, err := temporary.Write(content); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryName, path)
}

func Load(identifier string) ([]byte, error) {
	path, err := pathFor(identifier)
	if err != nil {
		return nil, err
	}
	content, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("draft not found; run lucien job %s", identifier)
	}
	return content, err
}

func Delete(identifier string) error {
	path, err := pathFor(identifier)
	if err != nil {
		return err
	}
	err = os.Remove(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}
