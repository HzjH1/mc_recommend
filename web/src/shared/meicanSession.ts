import { computed, ref } from 'vue';

const STORAGE_KEY = 'meal-helper-meican-session';

export type MeicanSession = {
  phone: string;
  accessToken: string;
  refreshToken: string;
  ticket: string;
  snowflakeId: string;
  signature: string;
  selectedAccountName: string;
  accountNamespace: string;
  accountNamespaceLunch: string;
  accountNamespaceDinner: string;
  accessTokenExpiresIn: number;
};

const DEFAULT_MEICAN_SESSION: MeicanSession = {
  phone: '',
  accessToken: '',
  refreshToken: '',
  ticket: '',
  snowflakeId: '',
  signature: '',
  selectedAccountName: '',
  accountNamespace: '',
  accountNamespaceLunch: '',
  accountNamespaceDinner: '',
  accessTokenExpiresIn: 3600,
};

function readStorage() {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeStorage(session: MeicanSession) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

export function getMeicanSession() {
  const raw = readStorage();
  return raw ? { ...DEFAULT_MEICAN_SESSION, ...raw } : null;
}

export function saveMeicanSession(session: Partial<MeicanSession>) {
  const normalized = { ...DEFAULT_MEICAN_SESSION, ...session };
  writeStorage(normalized);
  sessionState.value = normalized;
  return normalized;
}

export function clearMeicanSession() {
  if (typeof window !== 'undefined') {
    window.localStorage.removeItem(STORAGE_KEY);
  }
  sessionState.value = { ...DEFAULT_MEICAN_SESSION };
  return null;
}

export const sessionState = ref<MeicanSession>(getMeicanSession() || { ...DEFAULT_MEICAN_SESSION });
export const hasMeicanSession = computed(() => !!sessionState.value.accessToken);
