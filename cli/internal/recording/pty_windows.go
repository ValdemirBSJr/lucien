//go:build windows

package recording

import "errors"

func Start(_, _, _ string) (Session, error) {
	return Session{}, errors.New("PTY is not supported on Windows; run Lucien on Linux or macOS")
}

func processExists(_ int) bool { return false }

func terminateProcess(_ int) error {
	return errors.New("PTY termination is not natively supported on Windows")
}

func killProcess(_ int) error {
	return errors.New("PTY termination is not natively supported on Windows")
}
