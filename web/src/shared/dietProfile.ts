import { computed, ref } from 'vue';
import { formatText, getTranslations, type Language } from './i18n';
import { phoneEncryption } from './util';

const STORAGE_KEY = 'meal-helper-profile';

export const STAPLE_PREFERENCE_VALUES = ['noodle', 'rice', 'burger', 'other'] as const;

export type StaplePreference = (typeof STAPLE_PREFERENCE_VALUES)[number];

export type DietProfile = {
  phone: string;
  prefersSpicy: boolean;
  isHalal: boolean;
  isCutting: boolean;
  staplePreferences: StaplePreference[];
  otherNotes: string;
  staple: string;
  taboo: string;
  language: Language;
  email: string;
  balance: string;
  avatarText: string;
  meicanMemberId: string;
  meicanExternalMemberId: string;
  meicanName: string;
  meicanEmployeeNo: string;
  corpNames: string[];
  meicanCorpNamespace: string;
  userType: string;
  accountStatus: string;
};

export const DEFAULT_DIET_PROFILE: DietProfile = {
  phone: '',
  prefersSpicy: false,
  isHalal: false,
  isCutting: false,
  staplePreferences: ['rice'],
  otherNotes: '',
  staple: 'rice',
  taboo: '',
  language: 'zh-CN',
  email: '',
  balance: '',
  avatarText: '食',
  meicanMemberId: '',
  meicanExternalMemberId: '',
  meicanName: '',
  meicanEmployeeNo: '',
  corpNames: [],
  meicanCorpNamespace: '',
  userType: '',
  accountStatus: '',
};

export const LANGUAGE_OPTIONS = [
  { label: '简体中文', value: 'zh-CN' as Language },
  { label: 'English', value: 'en-US' as Language },
];

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

function writeStorage(profile: DietProfile) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
}

function normalizeSingleStapleValue(value = '') {
  const raw = `${value || ''}`.trim().toLowerCase();
  if (!raw) {
    return '';
  }
  if (['noodle', 'mian', 'fen', '面', '粉', '米粉', '面食'].some((item) => raw.includes(item))) {
    return 'noodle';
  }
  if (['rice', 'fan', '米饭', '饭'].some((item) => raw.includes(item))) {
    return 'rice';
  }
  if (['burger', 'hamburger', '汉堡'].some((item) => raw.includes(item))) {
    return 'burger';
  }
  if (['other', '其他'].some((item) => raw.includes(item))) {
    return 'other';
  }
  return '';
}

function hashString(value = '') {
  return `${value}`.split('').reduce((total, char, index) => {
    return total + char.charCodeAt(0) * (index + 1);
  }, 0);
}

function buildEmail(phone = '') {
  if (!phone) {
    return 'guest@meal.local';
  }
  return `meal${phone.slice(-4)}@meal.local`;
}

function buildBalance(phone = '') {
  const hash = hashString(phone || 'meal-helper');
  const amount = 88 + (hash % 240) + (hash % 100) / 100;
  return amount.toFixed(2);
}

function normalizeLanguage(language?: string): Language {
  return language === 'en-US' ? 'en-US' : 'zh-CN';
}

export function normalizeStaplePreferences(value: unknown, legacyStaple = ''): StaplePreference[] {
  const source = Array.isArray(value)
    ? value
    : `${value || ''}`
        .split(/[，,\s/]+/)
        .filter(Boolean);

  const normalized = source
    .map((item) => normalizeSingleStapleValue(`${item || ''}`))
    .filter(Boolean)
    .filter((item, index, list) => list.indexOf(item) === index) as StaplePreference[];

  if (normalized.length) {
    return normalized;
  }

  const legacy = normalizeSingleStapleValue(legacyStaple) as StaplePreference | '';
  return legacy ? [legacy] : ['rice'];
}

export function derivePrimaryStaple(staplePreferences: string[] = [], fallback = 'rice') {
  const normalized = normalizeStaplePreferences(staplePreferences, fallback);
  if (normalized.includes('noodle')) {
    return 'noodle';
  }
  if (normalized.includes('rice')) {
    return 'rice';
  }
  return normalizeSingleStapleValue(fallback) || 'rice';
}

export function getLanguageLabel(language = 'zh-CN') {
  const target = LANGUAGE_OPTIONS.find((item) => item.value === language);
  return target ? target.label : '简体中文';
}

export function normalizeDietProfile(profile: Partial<DietProfile> = {}): DietProfile {
  const normalized = {
    ...DEFAULT_DIET_PROFILE,
    ...profile,
  } as DietProfile;

  normalized.phone = `${normalized.phone || ''}`.trim();
  normalized.language = normalizeLanguage(normalized.language);
  normalized.taboo = `${normalized.taboo || ''}`.trim();
  normalized.otherNotes = `${normalized.otherNotes || normalized.taboo || ''}`.trim();
  normalized.staplePreferences = normalizeStaplePreferences(normalized.staplePreferences, normalized.staple);
  normalized.staple = derivePrimaryStaple(normalized.staplePreferences, normalized.staple);
  normalized.email = normalized.email || buildEmail(normalized.phone);
  normalized.balance = normalized.balance || buildBalance(normalized.phone);
  normalized.avatarText = normalized.avatarText || '食';
  normalized.meicanCorpNamespace = `${normalized.meicanCorpNamespace || ''}`.trim();

  return normalized;
}

export function getDietProfile() {
  const raw = readStorage();
  return raw ? normalizeDietProfile(raw) : null;
}

export function saveDietProfile(profile: Partial<DietProfile>) {
  const normalized = normalizeDietProfile(profile);
  writeStorage(normalized);
  profileState.value = normalized;
  return normalized;
}

export function refreshDietProfile() {
  profileState.value = getDietProfile() || { ...DEFAULT_DIET_PROFILE };
}

export function hasDietProfile() {
  return !!profileState.value.phone;
}

export function getMaskedPhone(profile: Partial<DietProfile> = {}) {
  if (!profile.phone) {
    return getTranslations(profile.language).meals.unknownPhone;
  }
  return phoneEncryption(profile.phone);
}

export function getPreferenceTags(profile: Partial<DietProfile> = {}) {
  if (!profile.phone) {
    return [];
  }

  const dict = getTranslations(profile.language).tags;
  const staplePreferences = normalizeStaplePreferences(profile.staplePreferences, profile.staple);
  const tags = [
    profile.prefersSpicy ? dict.spicy_yes : dict.spicy_no,
    profile.isHalal ? dict.halal_yes : dict.halal_no,
    profile.isCutting ? dict.cutting_yes : dict.cutting_no,
  ];

  staplePreferences.forEach((item) => {
    const key = `staple_${item}` as keyof typeof dict;
    if (dict[key]) {
      tags.push(dict[key]);
    }
  });

  if (profile.otherNotes) {
    tags.push(formatText(dict.otherNotes, { value: profile.otherNotes }));
  }

  return tags;
}

export const profileState = ref<DietProfile>(getDietProfile() || { ...DEFAULT_DIET_PROFILE });
export const currentLanguage = computed<Language>(() => normalizeLanguage(profileState.value.language));
export const hasProfile = computed(() => !!profileState.value.phone);
