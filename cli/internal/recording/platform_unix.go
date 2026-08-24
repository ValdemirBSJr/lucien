//go:build !windows

package recording

import (
	"errors"
	"os"
	"syscall"

	"github.com/lucien-runbook/lucien/internal/config"
)

func stateDirectory() (string, error) {
	return config.StateDir()
}

func processExists(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

func terminateProcess(pid int) error {
	if err := syscall.Kill(pid, syscall.SIGTERM); err != nil && !errors.Is(err, os.ErrProcessDone) {
		return err
	}
	return nil
}

func killProcess(pid int) error {
	if err := syscall.Kill(pid, syscall.SIGKILL); err != nil && !errors.Is(err, os.ErrProcessDone) {
		return err
	}
	return nil
}
