package main

import (
	"encoding/base64"
	"strings"
	"testing"
)

// 1x1 PNG transparente, o menor payload que ainda passa por http.DetectContentType.
const onePixelPNGBase64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="

func TestEncodeImageAssetReconhecePNG(t *testing.T) {
	content, err := base64.StdEncoding.DecodeString(onePixelPNGBase64)
	if err != nil {
		t.Fatalf("decodificar fixture: %v", err)
	}
	asset, err := encodeImageAsset(content)
	if err != nil {
		t.Fatalf("codificar imagem: %v", err)
	}
	if asset.MediaType != "image/png" {
		t.Fatalf("tipo de midia inesperado: %q", asset.MediaType)
	}
	if !strings.HasSuffix(asset.Filename, ".png") {
		t.Fatalf("extensao inesperada: %q", asset.Filename)
	}
	if asset.ContentBase64 != onePixelPNGBase64 {
		t.Fatal("conteudo codificado nao bate com o original")
	}
}

func TestEncodeImageAssetRecusaTipoNaoSuportado(t *testing.T) {
	_, err := encodeImageAsset([]byte("nao e uma imagem"))
	if err == nil {
		t.Fatal("deveria recusar conteudo que nao e PNG nem JPEG")
	}
}

func TestEncodeImageAssetGeraNomesUnicos(t *testing.T) {
	content, err := base64.StdEncoding.DecodeString(onePixelPNGBase64)
	if err != nil {
		t.Fatalf("decodificar fixture: %v", err)
	}
	first, err := encodeImageAsset(content)
	if err != nil {
		t.Fatalf("codificar imagem: %v", err)
	}
	second, err := encodeImageAsset(content)
	if err != nil {
		t.Fatalf("codificar imagem: %v", err)
	}
	if first.Filename == second.Filename {
		t.Fatalf("nomes deveriam ser unicos: %q == %q", first.Filename, second.Filename)
	}
}
