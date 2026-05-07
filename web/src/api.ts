type ApiResponse<T> = {
  code: number;
  message: string;
  requestId: string;
  data: T;
};

function normalizeBase(base?: string) {
  return `${base || ''}`.trim().replace(/\/$/, '');
}

function buildUrl(path: string) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const envBase = normalizeBase(import.meta.env.VITE_API_BASE as string | undefined);
  return envBase ? `${envBase}${normalizedPath}` : normalizedPath;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    throw new Error(`HTTP_${response.status}`);
  }

  const body = (await response.json()) as ApiResponse<T> | T;
  if (body && typeof body === 'object' && 'code' in body && typeof (body as ApiResponse<T>).code === 'number') {
    const result = body as ApiResponse<T>;
    if (result.code !== 0) {
      throw new Error(result.message || 'API_ERROR');
    }
    return result.data;
  }

  return body as T;
}

export function hasRecommendApi() {
  return true;
}

export type DailyRecItem = {
  rankNo: number;
  menuItemId: number;
  dishName: string;
  restaurantName: string;
  priceCent: number;
  score: number;
  reason: string;
  ordered: boolean;
};

export type DailyRecommendations = {
  date: string;
  LUNCH: DailyRecItem[];
  DINNER: DailyRecItem[];
};

export type WeeklyMenuDish = {
  id: number | string;
  name: string;
  price: string;
  status: string;
  statusText?: string;
};

export type WeeklyMenuSection = {
  key: string;
  tabUniqueId: string;
  targetTime: string;
  title: string;
  restaurants: Array<{
    id: string;
    name: string;
    distance?: number;
    distanceText?: string;
    status?: string;
    statusText?: string;
    menus: WeeklyMenuDish[];
  }>;
  recommendedDishes: WeeklyMenuDish[];
};

export type WeeklyMenuData = {
  namespace: string;
  weekDates: Array<{ dateKey: string; label: string; weekLabel: string; isToday?: boolean }>;
  selectedDate: string;
  mealSections: WeeklyMenuSection[];
};

export type AddressOption = {
  userAddressUniqueId: string;
  corpAddressUniqueId: string;
  label: string;
};

export type UserPreferences = {
  prefersSpicy: boolean;
  isHalal: boolean;
  isCutting: boolean;
  staple: string;
  staplePreferences?: string[];
  preferNoodle?: boolean;
  preferRice?: boolean;
  preferBurger?: boolean;
  other?: string;
  taboo: string;
  priceMin?: number | null;
  priceMax?: number | null;
};

export type AutoOrderConfig = {
  enabled: boolean;
  mealSlots: ('LUNCH' | 'DINNER')[];
  strategy: string;
  defaultCorpAddressId: string;
  defaultCorpAddressIdLunch: string;
  defaultCorpAddressIdDinner: string;
  effectiveFrom: string | null;
  effectiveTo: string | null;
};

export async function getDailyRecommendations(
  userId: string,
  params: { date: string; namespace?: string } ,
): Promise<DailyRecommendations> {
  const qs = new URLSearchParams();
  qs.set('date', params.date);
  if (`${params.namespace || ''}`.trim()) {
    qs.set('namespace', `${params.namespace || ''}`.trim());
  }
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/recommendations/daily?${qs.toString()}`, {
    method: 'GET',
  });
}

export async function getWeeklyMenus(
  userId: string,
  params: { date?: string; namespace?: string; sync?: boolean } = {},
): Promise<WeeklyMenuData> {
  const qs = new URLSearchParams();
  if (`${params.date || ''}`.trim()) {
    qs.set('date', `${params.date || ''}`.trim());
  }
  if (`${params.namespace || ''}`.trim()) {
    qs.set('namespace', `${params.namespace || ''}`.trim());
  }
  if (params.sync) {
    qs.set('sync', '1');
  }
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/menu/weekly${suffix}`, { method: 'GET' });
}

export async function getUserPreferences(userId: string): Promise<UserPreferences> {
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/preferences`, { method: 'GET' });
}

export async function putUserPreferences(userId: string, payload: Partial<UserPreferences>): Promise<void> {
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/preferences`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function putMeicanSession(
  userId: string,
  payload: {
    phone?: string;
    accessToken: string;
    refreshToken?: string;
    meicanUsername?: string;
    accountNamespace?: string;
    accountNamespaceLunch?: string;
    accountNamespaceDinner?: string;
    expiresIn?: number;
  },
): Promise<void> {
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/meican-session`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function getUserOrderAddresses(
  userId: string,
  params: { namespace?: string; mealSlot?: 'LUNCH' | 'DINNER' } = {},
): Promise<{ namespace: string; options: AddressOption[]; selectedCorpAddressId: string }> {
  const qs = new URLSearchParams();
  if (`${params.namespace || ''}`.trim()) {
    qs.set('namespace', `${params.namespace || ''}`.trim());
  }
  if (`${params.mealSlot || ''}`.trim()) {
    qs.set('mealSlot', `${params.mealSlot || ''}`.trim().toUpperCase());
  }
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/order-addresses${suffix}`, { method: 'GET' });
}

export async function getAutoOrderConfig(userId: string): Promise<AutoOrderConfig> {
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/auto-order-config`, { method: 'GET' });
}

export async function putAutoOrderConfig(
  userId: string,
  payload: Partial<AutoOrderConfig>,
): Promise<void> {
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/auto-order-config`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function postManualOrder(
  userId: string,
  payload: {
    date: string;
    mealSlot: 'LUNCH' | 'DINNER';
    namespace?: string;
    menuItemId: number;
    idempotencyKey: string;
    replace?: boolean;
    corpAddressUniqueId?: string;
    userAddressUniqueId?: string;
    defaultCorpAddressId?: string;
  },
): Promise<void> {
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/orders`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function postManualOrderCancel(
  userId: string,
  payload: { date: string; mealSlot: 'LUNCH' | 'DINNER'; orderUniqueId?: string },
): Promise<void> {
  return requestJson(`/api/v1/users/${encodeURIComponent(userId)}/orders/cancel`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
