import { derived, writable } from 'svelte/store';
import pt from './pt.json';
import en from './en.json';

export type Locale = 'pt' | 'en';
type Dict = typeof pt;

const dictionaries: Record<Locale, Dict> = { pt, en };
const STORAGE_KEY = 'lucien-desktop:locale';

function isLocale(value: string | null): value is Locale {
  return value === 'pt' || value === 'en';
}

function detectInitial(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isLocale(stored)) return stored;
  } catch {
    // segue para deteccao por idioma do sistema
  }
  return navigator.language?.toLowerCase().startsWith('pt') ? 'pt' : 'en';
}

export const locale = writable<Locale>(detectInitial());

locale.subscribe((value) => {
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // sem persistencia, so nao sobrevive ao reinicio
  }
});

export const t = derived(locale, ($locale) => {
  const dict = dictionaries[$locale] ?? dictionaries.en;
  return (key: keyof Dict, params?: Record<string, string>): string => {
    const template = dict[key] ?? key;
    if (!params) return template;
    return Object.entries(params).reduce(
      (text, [name, value]) => text.replaceAll(`{${name}}`, value),
      template,
    );
  };
});
