// Package connection guarda o endereço do Hub e o arquivo de CA usados pelo
// app grafico.
//
// O CLI de terminal le API_HOST/TLS_CA_FILE de variavel de ambiente porque
// quem o roda ja tem um shell configurado. Um app aberto por icone nao tem
// isso -- precisa de uma tela de configuracao e de um lugar para lembrar a
// escolha entre uma sessao e outra.
package connection

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

type Settings struct {
	APIHost string `json:"api_host"`
	// Caminho absoluto para o arquivo de CA, no mesmo formato que TLS_CA_FILE
	// exige do CLI.
	CAFile string `json:"tls_ca_file"`
}

// userConfigDir existe para o teste poder substituir por um diretorio
// temporario -- os.UserConfigDir() le %AppData% no Windows e ignora
// XDG_CONFIG_HOME, que so vale em Linux/macOS.
var userConfigDir = os.UserConfigDir

func path() (string, error) {
	directory, err := userConfigDir()
	if err != nil {
		return "", err
	}
	// Mesmo diretorio de profile.json (cli/internal/config), arquivo proprio:
	// isto nao e algo que o CLI de terminal precisa, entao nao mexe no pacote dele.
	return filepath.Join(directory, "lucien", "desktop-connection.json"), nil
}

// Load devolve Settings{} sem erro quando nada foi configurado ainda -- o
// primeiro uso do app e exatamente essa tela em branco.
func Load() (Settings, error) {
	target, err := path()
	if err != nil {
		return Settings{}, err
	}
	data, err := os.ReadFile(target)
	if errors.Is(err, os.ErrNotExist) {
		return Settings{}, nil
	}
	if err != nil {
		return Settings{}, err
	}
	var settings Settings
	if err := json.Unmarshal(data, &settings); err != nil {
		return Settings{}, err
	}
	return settings, nil
}

// Forget apaga a configuracao de conexao -- parte do "esquecer tudo" da tela
// de Configuracoes. Sem arquivo salvo e um no-op bem-sucedido.
func Forget() error {
	target, err := path()
	if err != nil {
		return err
	}
	if err := os.Remove(target); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

func Save(settings Settings) error {
	if settings.APIHost == "" || settings.CAFile == "" {
		return errors.New("api host and CA file are required")
	}
	target, err := path()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		return err
	}
	data, err := json.Marshal(settings)
	if err != nil {
		return err
	}
	return writeAtomic(target, data)
}

// writeAtomic evita deixar um arquivo pela metade se o processo cair no meio
// da escrita -- mesma tecnica que cli/internal/config ja usa para profile.json.
func writeAtomic(target string, data []byte) error {
	temporary, err := os.CreateTemp(filepath.Dir(target), ".lucien-desktop-*")
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
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryName, target)
}
