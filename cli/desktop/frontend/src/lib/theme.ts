import { writable } from 'svelte/store';

export type ThemeChoice = 'light' | 'dark' | 'system';

const STORAGE_KEY = 'lucien-desktop:theme';

function isThemeChoice(value: string | null): value is ThemeChoice {
  return value === 'light' || value === 'dark' || value === 'system';
}

function loadInitial(): ThemeChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return isThemeChoice(stored) ? stored : 'system';
  } catch {
    // localStorage pode falhar (perfil bloqueado); automatico e um padrao seguro.
    return 'system';
  }
}

function apply(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === 'system') {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', choice);
  }
}

export const theme = writable<ThemeChoice>(loadInitial());

theme.subscribe((value) => {
  apply(value);
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // Sem persistencia nao quebra a sessao atual; so nao sobrevive ao reinicio.
  }
});
