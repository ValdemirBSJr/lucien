package recording

import (
	"strings"
	"testing"
)

// Guarda de não-regressão para SSH em equipamento de rede. Cada cenário
// reproduz a forma com que o equipamento pagina, apaga e termina linha, e
// tem de sair igual com e sem a largura gravada: a largura só muda o
// tratamento do readline do jump, nunca o do equipamento.
func TestStripANSIPreservaCapturaDeEquipamentos(t *testing.T) {
	casos := map[string]struct{ entrada, esperado string }{
		// Huawei MA5800: o `---- More ----` é apagado recuando com ESC[nD,
		// sobrescrevendo com espaços e recuando de novo -- como na tela.
		"huawei_more": {
			"MA5800-X7#display board 0\r\n" +
				"  SlotID  BoardName  Status\r\n" +
				"  ---- More ( Press 'Q' to break ) ----" +
				"\x1b[37D" + strings.Repeat(" ", 37) + "\x1b[37D" +
				"  1       H901GPHF   Normal\r\n" +
				"MA5800-X7#quit\r\n",
			"MA5800-X7#display board 0\n" +
				"  SlotID  BoardName  Status\n" +
				"    1       H901GPHF   Normal          \n" +
				"MA5800-X7#quit\n",
		},
		// ZTE C300: `--More--` apagado com \b, espaços, \b.
		"zte_more": {
			"ZXAN#show card\r\nRack Shelf Slot CfgType\r\n" +
				"--More--" + strings.Repeat("\b", 8) + strings.Repeat(" ", 8) + strings.Repeat("\b", 8) +
				"1    1     3    GTGO\r\nZXAN#exit\r\n",
			"ZXAN#show card\nRack Shelf Slot CfgType\n1    1     3    GTGO\nZXAN#exit\n",
		},
		// Cisco cBR-8: a mesma forma, com espaço em volta.
		"cisco_more": {
			"cbr8#show cable modem summary\r\nInterface  Total  Active\r\n" +
				" --More-- " + strings.Repeat("\b", 10) + strings.Repeat(" ", 10) + strings.Repeat("\b", 10) +
				"Cable1/0/0  120    118\r\ncbr8#exit\r\n",
			"cbr8#show cable modem summary\nInterface  Total  Active\nCable1/0/0  120    118\ncbr8#exit\n",
		},
		// `--More--` apagado com \r, espaços e \r: o espaço antes do \r não
		// está na margem, então o \r continua fechando a linha.
		"more_por_retorno": {
			"cbr8#show version\r\n--More--\r        \rCisco IOS XE\r\ncbr8#exit\r\n",
			"cbr8#show version\n--More--\n        \nCisco IOS XE\ncbr8#exit\n",
		},
		// Login SSH a partir do jump, com senha e banner.
		"ssh_login": {
			"operador@jump:~$ ssh admin@olt.example.internal\r\n" +
				"admin@olt.example.internal's password: \r\n" +
				"\r\nWarning: authorized users only\r\n\r\n" +
				"MA5800-X7>enable\r\nMA5800-X7#display version\r\n" +
				"VERSION : MA5800V100R021\r\n" +
				"MA5800-X7#quit\r\nConnection to olt.example.internal closed.\r\n" +
				"operador@jump:~$ echo fim\r\nfim\r\n",
			"operador@jump:~$ ssh admin@olt.example.internal\n" +
				"admin@olt.example.internal's password: \n" +
				"\nWarning: authorized users only\n\n" +
				"MA5800-X7>enable\nMA5800-X7#display version\n" +
				"VERSION : MA5800V100R021\n" +
				"MA5800-X7#quit\nConnection to olt.example.internal closed.\n" +
				"operador@jump:~$ echo fim\nfim\n",
		},
		// Erro de digitação corrigido com backspace no equipamento.
		"cisco_backspace": {
			"cbr8#show runn\b \bning-config | include hostname\r\nhostname cbr8\r\ncbr8#exit\r\n",
			"cbr8#show running-config | include hostname\nhostname cbr8\ncbr8#exit\n",
		},
		// ZTE com \r puro como fim de linha.
		"zte_cr": {
			"ZTE#show card\rSlot 1 GTGH\rSlot 2 GTGO\r",
			"ZTE#show card\nSlot 1 GTGH\nSlot 2 GTGO\n",
		},
		// Huawei com cor e modo de configuração no prompt.
		"huawei_config": {
			"\x1b[0mMA5800-X7(config)#\x1b[0minterface gpon 0/1\r\n" +
				"MA5800-X7(config-if-gpon-0/1)#display ont info 0 all\r\n" +
				"  F/S/P   ONT-ID   SN\r\n" +
				"MA5800-X7(config-if-gpon-0/1)#quit\r\n",
			"MA5800-X7(config)#interface gpon 0/1\n" +
				"MA5800-X7(config-if-gpon-0/1)#display ont info 0 all\n" +
				"  F/S/P   ONT-ID   SN\n" +
				"MA5800-X7(config-if-gpon-0/1)#quit\n",
		},
	}
	for nome, caso := range casos {
		for rotulo, prefixo := range map[string]string{
			"sem largura": "",
			"com largura": string(marcadorLargura(80)),
		} {
			if got := StripANSI(prefixo + caso.entrada); got != caso.esperado {
				t.Fatalf("%s (%s):\n got  %q\n want %q", nome, rotulo, got, caso.esperado)
			}
		}
	}
}
