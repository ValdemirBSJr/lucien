import { writable } from 'svelte/store';
import { AuthStatus, IsConnectionConfigured } from '../../wailsjs/go/main/App';
import type { main } from '../../wailsjs/go/models';

export type SessionPhase =
  | 'checking'
  | 'needs_connection'
  | 'hub_unreachable'
  | 'signed_out'
  | 'signed_in';

export const sessionPhase = writable<SessionPhase>('checking');
export const identity = writable<main.Identity | null>(null);

// Roda na abertura do app (e depois de salvar conexao/login) para decidir
// qual tela mostrar, sem que o operador precise pedir nada.
export async function refreshSession(): Promise<void> {
  let configured: boolean;
  try {
    configured = await IsConnectionConfigured();
  } catch {
    sessionPhase.set('needs_connection');
    return;
  }
  if (!configured) {
    sessionPhase.set('needs_connection');
    return;
  }
  // AuthStatus nao lanca: descreve o estado. Um Hub inalcancavel caia no mesmo
  // catch de "sem credencial" e o app abria a tela de token -- quem estava sem
  // rede era mandado digitar um token que nao resolveria nada.
  let probe: main.SessionProbe;
  try {
    probe = await AuthStatus();
  } catch {
    // A chamada em si falhou, nao a rede ate o Hub. Sem saber distinguir, o
    // token continua sendo a hipotese menos alarmante.
    identity.set(null);
    sessionPhase.set('signed_out');
    return;
  }
  if (probe.unreachable) {
    identity.set(null);
    sessionPhase.set('hub_unreachable');
    return;
  }
  if (probe.identity) {
    identity.set(probe.identity);
    sessionPhase.set('signed_in');
    return;
  }
  identity.set(null);
  sessionPhase.set('signed_out');
}
