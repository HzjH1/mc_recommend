type ApiResponse<T> = {
  code: number;
  message: string;
  requestId: string;
  data: T;
};

function normalizeBase(base: string) {
  return (base || '').trim().replace(/\/$/, '');
}

async function requestJson<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const b = normalizeBase(base);
  if (!b) throw new Error('API_BASE_NOT_SET');
  const url = `${b}${path.startsWith('/') ? path : `/${path}`}`;
  const resp = await fetch(url, {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(init?.headers || {}),
    },
  });
  if (!resp.ok) throw new Error(`HTTP_${resp.status}`);
  const body = (await resp.json()) as ApiResponse<T> | T;
  if (body && typeof body === 'object' && 'code' in body && typeof (body as any).code === 'number') {
    const br = body as ApiResponse<T>;
    if (br.code !== 0) throw new Error(br.message || 'API_ERROR');
    return br.data;
  }
  return body as T;
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

export type AddressOption = {
  userAddressUniqueId: string;
  corpAddressUniqueId: string;
  label: string;
};

export async function getDailyRecommendations(
  base: string,
  userId: string,
  date: string,
  namespace?: string,
): Promise<DailyRecommendations> {
  const qs = new URLSearchParams();
  qs.set('date', date);
  if ((namespace || '').trim()) qs.set('namespace', (namespace || '').trim());
  return requestJson(base, `/api/v1/users/${encodeURIComponent(userId)}/recommendations/daily?${qs.toString()}`, {
    method: 'GET',
  });
}

export async function getUserOrderAddresses(
  base: string,
  userId: string,
  namespace?: string,
  mealSlot?: string,
): Promise<{ namespace: string; options: AddressOption[]; selectedCorpAddressId: string }> {
  const qs = new URLSearchParams();
  if ((namespace || '').trim()) qs.set('namespace', (namespace || '').trim());
  if ((mealSlot || '').trim()) qs.set('mealSlot', (mealSlot || '').trim().toUpperCase());
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return requestJson(base, `/api/v1/users/${encodeURIComponent(userId)}/order-addresses${suffix}`, { method: 'GET' });
}

export async function postManualOrder(
  base: string,
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
): Promise<any> {
  return requestJson(base, `/api/v1/users/${encodeURIComponent(userId)}/orders`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function postManualOrderCancel(
  base: string,
  userId: string,
  payload: { date: string; mealSlot: 'LUNCH' | 'DINNER'; orderUniqueId?: string },
): Promise<any> {
  return requestJson(base, `/api/v1/users/${encodeURIComponent(userId)}/orders/cancel`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

