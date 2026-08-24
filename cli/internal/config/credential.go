package config

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/zalando/go-keyring"
)

const (
	credentialService = "lucien-runbook-api"
	storeKeyring      = "keyring"
	storeFile         = "arquivo_chmod_600"
	keyringTimeout    = 3 * time.Second
)

func SaveAuthenticatedProfile(profile Profile, apiHost, token string) error {
	if token == "" || apiHost == "" {
		return errors.New("token and API_HOST are required")
	}
	account := credentialAccount(apiHost, profile.UserID)
	store, err := storeToken(account, token)
	if err != nil {
		return err
	}
	profile.CredentialStore = store
	if err := SaveProfile(profile); err != nil {
		_ = deleteStoredToken(store, account)
		return fmt.Errorf("save profile metadata: %w", err)
	}
	return nil
}

func LoadAuthenticatedProfile(apiHost string) (Profile, string, error) {
	profile, legacyToken, err := loadProfileDisk()
	if err != nil {
		return Profile{}, "", err
	}
	if profile.CredentialStore == "" && legacyToken != "" {
		// Migração única: retira a credencial de profile.json e grava no backend seguro.
		if err := SaveAuthenticatedProfile(profile, apiHost, legacyToken); err != nil {
			return Profile{}, "", fmt.Errorf("migrate legacy credential: %w", err)
		}
		return LoadAuthenticatedProfile(apiHost)
	}
	return loadCurrentToken(profile, apiHost)
}

func loadCurrentToken(profile Profile, apiHost string) (Profile, string, error) {
	account := credentialAccount(apiHost, profile.UserID)
	var (
		token string
		err   error
	)
	switch profile.CredentialStore {
	case storeKeyring:
		token, err = keyringGet(account)
	case storeFile:
		token, err = readTokenFile(account)
	default:
		return Profile{}, "", errors.New("profile credential backend is invalid")
	}
	if err != nil {
		return Profile{}, "", fmt.Errorf("load token: %w", err)
	}
	if token == "" {
		return Profile{}, "", errors.New("stored token is empty")
	}
	return profile, token, nil
}

func storeToken(account, token string) (string, error) {
	if err := keyringSet(account, token); err == nil {
		return storeKeyring, nil
	} else if !allowFileFallback() {
		return "", fmt.Errorf(
			"system keyring unavailable: %w; enable LUCIEN_ALLOW_FILE_TOKEN=true only on a controlled Unix host",
			err,
		)
	}
	if runtime.GOOS == "windows" {
		return "", errors.New("file fallback is prohibited on Windows; fix the Credential Manager")
	}
	if err := writeTokenFile(account, token); err != nil {
		return "", err
	}
	return storeFile, nil
}

func allowFileFallback() bool {
	return strings.EqualFold(os.Getenv("LUCIEN_ALLOW_FILE_TOKEN"), "true")
}

func credentialAccount(apiHost, userID string) string {
	digest := sha256.Sum256([]byte(apiHost + "\x00" + userID))
	return hex.EncodeToString(digest[:])
}

func credentialDirectory() (string, error) {
	directory, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(directory, "lucien", "credentials"), nil
}

func tokenFilePath(account string) (string, error) {
	directory, err := credentialDirectory()
	if err != nil {
		return "", err
	}
	return filepath.Join(directory, account+".token"), nil
}

func writeTokenFile(account, token string) error {
	path, err := tokenFilePath(account)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("create credential directory: %w", err)
	}
	return writeAtomic(path, []byte(token), 0o600)
}

func readTokenFile(account string) (string, error) {
	path, err := tokenFilePath(account)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	if info.Mode().Perm()&0o077 != 0 {
		return "", errors.New("token file has insecure permissions; chmod 600 is required")
	}
	if info.Size() > 4096 {
		return "", errors.New("token file exceeds the safe size limit")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(content)), nil
}

func deleteStoredToken(store, account string) error {
	if store == storeKeyring {
		return keyringDelete(account)
	}
	if store == storeFile {
		path, err := tokenFilePath(account)
		if err != nil {
			return err
		}
		return os.Remove(path)
	}
	return nil
}

func keyringSet(account, token string) error {
	result := make(chan error, 1)
	go func() { result <- keyring.Set(credentialService, account, token) }()
	select {
	case err := <-result:
		return err
	case <-time.After(keyringTimeout):
		return errors.New("timed out accessing the keyring")
	}
}

func keyringGet(account string) (string, error) {
	type response struct {
		value string
		err   error
	}
	result := make(chan response, 1)
	go func() {
		value, err := keyring.Get(credentialService, account)
		result <- response{value: value, err: err}
	}()
	select {
	case response := <-result:
		return response.value, response.err
	case <-time.After(keyringTimeout):
		return "", errors.New("timed out accessing the keyring")
	}
}

func keyringDelete(account string) error {
	result := make(chan error, 1)
	go func() { result <- keyring.Delete(credentialService, account) }()
	select {
	case err := <-result:
		return err
	case <-time.After(keyringTimeout):
		return errors.New("timed out accessing the keyring")
	}
}
