import { writable } from 'svelte/store';
import { AuthStatus, IsConnectionConfigured } from '../../wailsjs/go/main/App';
import type { main } from '../../wailsjs/go/models';

export type SessionPhase = 'checking' | 'needs_connection' | 'signed_out' | 'signed_in';

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
  try {
    const current = await AuthStatus();
    identity.set(current);
    sessionPhase.set('signed_in');
  } catch {
    identity.set(null);
    sessionPhase.set('signed_out');
  }
}
