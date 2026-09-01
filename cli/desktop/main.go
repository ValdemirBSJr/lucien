package main

import (
	"embed"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
)

//go:embed all:frontend/dist
var assets embed.FS

// Preenchida no build com -ldflags "-X main.version=...", como o CLI faz em
// scripts/build-cli.sh. O default declara que o binario nao veio de um build
// versionado, em vez de mentir uma versao.
var version = "dev"

// Espelham o bloco `info` do wails.json, que alimenta as propriedades do
// arquivo no Windows. Ficam aqui tambem porque a tela de configuracao mostra
// os mesmos tres dados, e um app que so os declara no manifesto nao consegue
// exibi-los.
const (
	productName = "Lucien Desktop"
	copyright   = "Copyright \u00a9 2026 Valdemir Bezerra de Souza Junior"
)

func main() {
	// Create an instance of the app structure
	app := NewApp()

	// Create application with options
	err := wails.Run(&options.App{
		Title:  "Lucien Desktop " + version,
		Width:  1024,
		Height: 768,
		AssetServer: &assetserver.Options{
			Assets: assets,
		},
		BackgroundColour: &options.RGBA{R: 27, G: 38, B: 54, A: 1},
		OnStartup:        app.startup,
		Bind: []interface{}{
			app,
		},
	})

	if err != nil {
		println("Error:", err.Error())
	}
}
