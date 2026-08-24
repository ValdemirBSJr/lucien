package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

type Profile struct {
	UserID          string `json:"user_id"`
	Username        string `json:"username"`
	CredentialStore string `json:"credential_store"`
}

type profileDisk struct {
	UserID          string `json:"user_id"`
	Username        string `json:"username"`
	LegacyName      string `json:"name"`
	CredentialStore string `json:"credential_store"`
	LegacyAPIKey    string `json:"api_key"`
}

func profilePath() (string, error) {
	directory, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(directory, "lucien", "profile.json"), nil
}

func SaveProfile(profile Profile) error {
	if profile.UserID == "" || profile.Username == "" || profile.CredentialStore == "" {
		return errors.New("local profile is incomplete")
	}
	path, err := profilePath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("create profile directory: %w", err)
	}
	data, err := json.Marshal(profile)
	if err != nil {
		return err
	}
	return writeAtomic(path, data, 0o600)
}

func loadProfileDisk() (Profile, string, error) {
	path, err := profilePath()
	if err != nil {
		return Profile{}, "", err
	}
	info, err := os.Stat(path)
	if errors.Is(err, os.ErrNotExist) {
		return Profile{}, "", errors.New("no user configured; run lucien login")
	}
	if err != nil {
		return Profile{}, "", err
	}
	if runtime.GOOS != "windows" && info.Mode().Perm()&0o077 != 0 {
		return Profile{}, "", errors.New("local profile has insecure permissions; chmod 600 is required")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return Profile{}, "", err
	}
	var disk profileDisk
	if err := json.Unmarshal(data, &disk); err != nil {
		return Profile{}, "", fmt.Errorf("invalid local profile: %w", err)
	}
	username := disk.Username
	if username == "" {
		username = disk.LegacyName
	}
	profile := Profile{
		UserID:          disk.UserID,
		Username:        username,
		CredentialStore: disk.CredentialStore,
	}
	if profile.UserID == "" || profile.Username == "" {
		return Profile{}, "", errors.New("local profile is incomplete")
	}
	return profile, disk.LegacyAPIKey, nil
}

func writeAtomic(path string, data []byte, mode os.FileMode) error {
	temporary, err := os.CreateTemp(filepath.Dir(path), ".lucien-*")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(mode); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryName, path)
}

func StateDir() (string, error) {
	if directory := os.Getenv("XDG_STATE_HOME"); directory != "" {
		return filepath.Join(directory, "lucien"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "state", "lucien"), nil
}
