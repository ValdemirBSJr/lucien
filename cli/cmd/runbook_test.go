package cmd

import "testing"

func TestReviseExigeUUIDCanonico(t *testing.T) {
	t.Parallel()

	// O comando aceita uma unica forma: nem indice de reviews, nem nome.
	aceitos := []string{
		"3e381ebe-0284-4d3b-b304-a13655e3dd4c",
		"52d1b673-06f4-45ac-96db-73a5a9cf11c0",
	}
	for _, id := range aceitos {
		if !canonicalRunbookID.MatchString(id) {
			t.Fatalf("UUID canonico deveria ser aceito: %q", id)
		}
	}

	recusados := []string{
		"1",
		"",
		"validar-placa-olt-hw",
		"validar-placa-olt-hw-20260817-234823-f198bde04ad1",
		"3E381EBE-0284-4D3B-B304-A13655E3DD4C",
		"3e381ebe02844d3bb304a13655e3dd4c",
		"3e381ebe-0284-4d3b-b304-a13655e3dd4",
		" 3e381ebe-0284-4d3b-b304-a13655e3dd4c",
	}
	for _, valor := range recusados {
		if canonicalRunbookID.MatchString(valor) {
			t.Fatalf("valor nao canonico foi aceito: %q", valor)
		}
	}
}
