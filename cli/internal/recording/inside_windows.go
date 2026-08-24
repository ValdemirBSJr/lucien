//go:build windows

package recording

// insideSession nunca é verdadeiro no Windows: o PTY não é suportado ali, então
// não existe sessão gravada da qual este processo possa descender.
func insideSession(int) bool { return false }
