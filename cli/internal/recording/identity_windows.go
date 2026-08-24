//go:build windows

package recording

// processIdentity não tem contrapartida no Windows, onde o PTY já não é
// suportado. Devolver "desconhecido" mantém o fluxo compilável sem fingir uma
// garantia que a plataforma não dá.
func processIdentity(int) (string, bool) { return "", false }
