<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  getAutoOrderConfig,
  getDailyRecommendations,
  getUserOrderAddresses,
  postManualOrder,
  postManualOrderCancel,
  putAutoOrderConfig,
  type AddressOption,
  type AutoOrderConfig,
} from '../api';
import { buildWorkweekDates } from '../shared/mealPlan';
import { currentLanguage, getPreferenceTags, hasProfile, LANGUAGE_OPTIONS, profileState, saveDietProfile } from '../shared/dietProfile';
import { getTranslations } from '../shared/i18n';
import { sessionState } from '../shared/meicanSession';
import { stableRecommendUserId } from '../shared/recommendUserId';
import { syncRecommendBackendSession } from '../shared/recommendSessionSync';
import { showToast } from '../shared/toast';

type RecommendRow = {
  id: number;
  rankNo: number;
  name: string;
  restaurant: string;
  price: string;
  reason: string;
  statusText: string;
  orderedFromRecommend: boolean;
  orderBtn: 'ordered' | 'place' | 'change';
};

type RecommendDay = {
  dateKey: string;
  dateLabel: string;
  weekLabel: string;
  isToday: boolean;
  lunchRecs: RecommendRow[];
  dinnerRecs: RecommendRow[];
};

const router = useRouter();
const loading = ref(false);
const autoOrderSaving = ref(false);
const error = ref('');
const recommendWeek = ref<RecommendDay[]>([]);
const selectedRecommendDate = ref('');
const lunchRecsShown = ref<RecommendRow[]>([]);
const dinnerRecsShown = ref<RecommendRow[]>([]);
const recommendDayTitle = ref('');
const recommendDayIsToday = ref(false);
const autoOrderEnabled = ref(false);
const autoOrderMeta = ref<AutoOrderConfig | null>(null);

const addressSheetOpen = ref(false);
const addressSheetTitle = ref('');
const addressSheetOptions = ref<AddressOption[]>([]);
let addressSheetResolve: ((value: AddressOption | null) => void) | null = null;

const texts = computed(() => getTranslations(currentLanguage.value).home);
const profile = computed(() => profileState.value);
const session = computed(() => sessionState.value);
const recommendUserId = computed(() => stableRecommendUserId());
const meicanLoggedIn = computed(() => !!(session.value.accessToken && session.value.phone === profile.value.phone));
const hasRecommendUser = computed(() => !!recommendUserId.value);
const profileTags = computed(() => (hasProfile.value ? getPreferenceTags(profile.value) : []));
const meicanStatusText = computed(() => (meicanLoggedIn.value ? texts.value.loginStatusBound : texts.value.loginStatusUnbound));
const languageIndex = computed(() => {
  const index = LANGUAGE_OPTIONS.findIndex((item) => item.value === currentLanguage.value);
  return index >= 0 ? index : 0;
});
const languageLabel = computed(() => LANGUAGE_OPTIONS[languageIndex.value]?.label || LANGUAGE_OPTIONS[0].label);

function activeNamespace(slot?: 'LUNCH' | 'DINNER') {
  if (slot === 'LUNCH' && `${session.value.accountNamespaceLunch || ''}`.trim()) {
    return `${session.value.accountNamespaceLunch || ''}`.trim();
  }
  if (slot === 'DINNER' && `${session.value.accountNamespaceDinner || ''}`.trim()) {
    return `${session.value.accountNamespaceDinner || ''}`.trim();
  }
  if (`${profile.value.meicanCorpNamespace || ''}`.trim()) {
    return `${profile.value.meicanCorpNamespace || ''}`.trim();
  }
  return `${session.value.accountNamespace || ''}`.trim();
}

function attachOrderButtons(rows: RecommendRow[]) {
  const hasOrderedRow = rows.some((row) => row.orderedFromRecommend);
  return rows.map((row) => ({
    ...row,
    orderBtn: row.orderedFromRecommend ? 'ordered' : hasOrderedRow ? 'change' : 'place',
  }));
}

function mapRecommendRows(list: any[] = []) {
  return attachOrderButtons(
    list.slice(0, 3).map((item) => ({
      id: Number(item.menuItemId),
      rankNo: Number(item.rankNo || 0),
      name: item.dishName || '',
      restaurant: `${item.restaurantName || ''}`.trim(),
      price:
        item.priceCent !== undefined && item.priceCent !== null && !Number.isNaN(Number(item.priceCent))
          ? (Number(item.priceCent) / 100).toFixed(2)
          : '',
      reason: `${item.reason || ''}`.trim(),
      statusText: item.ordered ? texts.value.orderedBadge : texts.value.recommendAvailable,
      orderedFromRecommend: !!item.ordered,
      orderBtn: 'place' as const,
    })),
  );
}

function updateSelectedDay() {
  const day =
    recommendWeek.value.find((item) => item.dateKey === selectedRecommendDate.value) ||
    recommendWeek.value.find((item) => item.isToday) ||
    recommendWeek.value[0];

  if (!day) {
    selectedRecommendDate.value = '';
    recommendDayTitle.value = '';
    recommendDayIsToday.value = false;
    lunchRecsShown.value = [];
    dinnerRecsShown.value = [];
    return;
  }

  selectedRecommendDate.value = day.dateKey;
  recommendDayTitle.value = `${day.dateLabel} · ${day.weekLabel}`;
  recommendDayIsToday.value = day.isToday;
  lunchRecsShown.value = day.lunchRecs;
  dinnerRecsShown.value = day.dinnerRecs;
}

async function chooseAddress(title: string, options: AddressOption[]) {
  if (!options.length) {
    return null;
  }

  addressSheetTitle.value = title;
  addressSheetOptions.value = options;
  addressSheetOpen.value = true;

  return new Promise<AddressOption | null>((resolve) => {
    addressSheetResolve = resolve;
  });
}

function closeAddressSheet(value: AddressOption | null) {
  addressSheetOpen.value = false;
  addressSheetTitle.value = '';
  addressSheetOptions.value = [];
  if (addressSheetResolve) {
    addressSheetResolve(value);
    addressSheetResolve = null;
  }
}

async function refreshPage() {
  error.value = '';

  if (!hasProfile.value) {
    recommendWeek.value = [];
    updateSelectedDay();
    autoOrderMeta.value = null;
    autoOrderEnabled.value = false;
    return;
  }

  if (!meicanLoggedIn.value) {
    recommendWeek.value = [];
    updateSelectedDay();
    autoOrderMeta.value = null;
    autoOrderEnabled.value = false;
    return;
  }

  if (!hasRecommendUser.value) {
    recommendWeek.value = [];
    updateSelectedDay();
    autoOrderMeta.value = null;
    autoOrderEnabled.value = false;
    return;
  }

  loading.value = true;

  try {
    await syncRecommendBackendSession(profile.value.phone);
    const skeleton = buildWorkweekDates(new Date(), currentLanguage.value);
    const namespace = activeNamespace() || undefined;
    const results = await Promise.all(
      skeleton.map((day) =>
        getDailyRecommendations(recommendUserId.value, {
          date: day.dateKey,
          namespace,
        }).catch(() => null),
      ),
    );

    recommendWeek.value = skeleton.map((day, index) => ({
      ...day,
      lunchRecs: mapRecommendRows(results[index]?.LUNCH || []),
      dinnerRecs: mapRecommendRows(results[index]?.DINNER || []),
    }));

    updateSelectedDay();

    try {
      const config = await getAutoOrderConfig(recommendUserId.value);
      autoOrderMeta.value = config;
      autoOrderEnabled.value = !!config.enabled;
    } catch {
      autoOrderMeta.value = {
        enabled: false,
        mealSlots: ['LUNCH', 'DINNER'],
        strategy: 'TOP1',
        defaultCorpAddressId: '',
        defaultCorpAddressIdLunch: '',
        defaultCorpAddressIdDinner: '',
        effectiveFrom: null,
        effectiveTo: null,
      };
      autoOrderEnabled.value = false;
    }
  } catch (err: any) {
    error.value = String(err?.message || err);
    recommendWeek.value = [];
    updateSelectedDay();
    autoOrderMeta.value = null;
    autoOrderEnabled.value = false;
  } finally {
    loading.value = false;
  }
}

function openPreferencePage(forceOnboarding = false) {
  router.push({
    path: '/preferences',
    query: forceOnboarding ? { mode: 'onboarding' } : undefined,
  });
}

function handleLanguageChange(event: Event) {
  const nextIndex = Number((event.target as HTMLSelectElement).value);
  const selected = LANGUAGE_OPTIONS[nextIndex];
  if (!selected) {
    return;
  }

  saveDietProfile({
    ...profile.value,
    language: selected.value,
  });
  showToast(getTranslations(selected.value).common.languageChanged);
  refreshPage();
}

async function handleAutoOrderChange(event: Event) {
  if (!hasRecommendUser.value || !autoOrderMeta.value) {
    return;
  }

  const enabled = !!(event.target as HTMLInputElement).checked;
  const previous = autoOrderMeta.value;
  const today = new Date().toISOString().slice(0, 10);

  autoOrderSaving.value = true;

  try {
    let lunchAddress: AddressOption | null = null;
    let dinnerAddress: AddressOption | null = null;

    if (enabled) {
      const [lunchAddresses, dinnerAddresses] = await Promise.all([
        getUserOrderAddresses(recommendUserId.value, {
          namespace: activeNamespace('LUNCH') || undefined,
          mealSlot: 'LUNCH',
        }).catch(() => ({ options: [], namespace: '', selectedCorpAddressId: '' })),
        getUserOrderAddresses(recommendUserId.value, {
          namespace: activeNamespace('DINNER') || undefined,
          mealSlot: 'DINNER',
        }).catch(() => ({ options: [], namespace: '', selectedCorpAddressId: '' })),
      ]);

      if (!lunchAddresses.options.length || !dinnerAddresses.options.length) {
        showToast(texts.value.orderAddressUnavailable);
        return;
      }

      lunchAddress = await chooseAddress('选择午餐地址', lunchAddresses.options);
      if (!lunchAddress) {
        showToast(texts.value.orderAddressNotSelected);
        return;
      }

      dinnerAddress = await chooseAddress('选择晚餐地址', dinnerAddresses.options);
      if (!dinnerAddress) {
        showToast(texts.value.orderAddressNotSelected);
        return;
      }
    }

    await putAutoOrderConfig(recommendUserId.value, {
      enabled,
      mealSlots: previous.mealSlots?.length ? previous.mealSlots : ['LUNCH', 'DINNER'],
      strategy: previous.strategy || 'TOP1',
      defaultCorpAddressId: previous.defaultCorpAddressId || '',
      defaultCorpAddressIdLunch: lunchAddress?.corpAddressUniqueId || previous.defaultCorpAddressIdLunch || '',
      defaultCorpAddressIdDinner: dinnerAddress?.corpAddressUniqueId || previous.defaultCorpAddressIdDinner || '',
      effectiveFrom: previous.effectiveFrom || today,
      effectiveTo: previous.effectiveTo || undefined,
    });

    autoOrderMeta.value = {
      ...previous,
      enabled,
      defaultCorpAddressIdLunch: lunchAddress?.corpAddressUniqueId || previous.defaultCorpAddressIdLunch || '',
      defaultCorpAddressIdDinner: dinnerAddress?.corpAddressUniqueId || previous.defaultCorpAddressIdDinner || '',
      effectiveFrom: previous.effectiveFrom || today,
    };
    autoOrderEnabled.value = enabled;
    showToast(getTranslations(currentLanguage.value).common.saveSuccess);
  } catch {
    showToast(getTranslations(currentLanguage.value).common.saveFailed);
  } finally {
    autoOrderSaving.value = false;
  }
}

async function handleRecommendOrderTap(slot: 'LUNCH' | 'DINNER', action: 'place' | 'change' | 'cancel', row: RecommendRow) {
  if (!hasRecommendUser.value || !selectedRecommendDate.value) {
    return;
  }

  const namespace = activeNamespace(slot);
  if (!namespace) {
    showToast(texts.value.orderNeedNamespace);
    return;
  }

  try {
    if (action === 'cancel') {
      await postManualOrderCancel(recommendUserId.value, {
        date: selectedRecommendDate.value,
        mealSlot: slot,
      });
      showToast(texts.value.orderCancelSuccess);
      await refreshPage();
      return;
    }

    if (!Number.isFinite(row.id)) {
      showToast(texts.value.orderInvalidMenuItemId);
      return;
    }

    const addressData = await getUserOrderAddresses(recommendUserId.value, {
      namespace,
      mealSlot: slot,
    });
    if (!addressData.options.length) {
      showToast(texts.value.orderAddressUnavailable);
      return;
    }

    const selectedAddress = await chooseAddress(slot === 'LUNCH' ? '选择午餐地址' : '选择晚餐地址', addressData.options);
    if (!selectedAddress) {
      showToast(texts.value.orderAddressNotSelected);
      return;
    }

    await postManualOrder(recommendUserId.value, {
      date: selectedRecommendDate.value,
      mealSlot: slot,
      menuItemId: row.id,
      namespace,
      replace: action === 'change',
      idempotencyKey: `web-${selectedRecommendDate.value}-${slot}-${row.id}-${Date.now()}`,
      corpAddressUniqueId: selectedAddress.corpAddressUniqueId,
      userAddressUniqueId: selectedAddress.userAddressUniqueId,
      defaultCorpAddressId: selectedAddress.corpAddressUniqueId,
    });

    showToast(texts.value.orderSuccess);
    await refreshPage();
  } catch {
    showToast(action === 'cancel' ? texts.value.orderCancelFailed : texts.value.orderFailed);
  }
}

function handleAutoOrderRuleTip() {
  window.alert(`${texts.value.autoOrderRuleTipTitle}\n\n${texts.value.autoOrderRuleTipContent}`);
}

onMounted(() => {
  refreshPage();
});
</script>

<template>
  <div class="page-shell">
    <div class="top-card">
      <div class="status-row">
        <span class="status-label">{{ texts.statusMeicanLabel }}</span>
        <span class="status-value" :class="{ 'status-on': meicanLoggedIn }">{{ meicanStatusText }}</span>
      </div>

      <div class="lang-row">
        <span class="status-label">{{ texts.languagePickerLabel }}</span>
        <div class="lang-picker-trigger">
          <select class="lang-select" :value="languageIndex" @change="handleLanguageChange">
            <option v-for="(item, index) in LANGUAGE_OPTIONS" :key="item.value" :value="index">
              {{ item.label }}
            </option>
          </select>
          <span class="lang-picker-value">{{ languageLabel }}</span>
          <span class="lang-picker-caret">▼</span>
        </div>
      </div>

      <button class="pref-entry" @click="openPreferencePage(!hasProfile)">
        {{ hasProfile ? texts.preferenceAction : texts.fillNow }}
      </button>
    </div>

    <div class="tag-list" v-if="hasProfile && profileTags.length">
      <div v-for="tag in profileTags" :key="tag" class="tag-item">{{ tag }}</div>
    </div>

    <div class="auto-order-card" v-if="hasProfile && meicanLoggedIn && hasRecommendUser">
      <div class="auto-order-row">
        <div class="auto-order-copy">
          <div class="auto-order-title-row">
            <div class="auto-order-title">{{ texts.autoOrderTitle }}</div>
            <button class="auto-order-tip-icon" @click="handleAutoOrderRuleTip">?</button>
          </div>
          <div class="auto-order-desc">{{ texts.autoOrderDesc }}</div>
        </div>

        <label class="switch-toggle">
          <input type="checkbox" :checked="autoOrderEnabled" :disabled="autoOrderSaving || loading" @change="handleAutoOrderChange" />
          <span class="switch-slider"></span>
        </label>
      </div>
    </div>

    <div class="empty-card" v-if="!hasProfile">
      <div class="empty-title">{{ texts.onboardingTitle }}</div>
      <div class="empty-desc">{{ texts.onboardingDesc }}</div>
      <button class="primary-button" @click="openPreferencePage(true)">{{ texts.fillNow }}</button>
    </div>

    <div class="empty-card" v-else-if="!meicanLoggedIn">
      <div class="empty-title">{{ texts.meicanRequiredTitle }}</div>
      <div class="empty-desc">{{ texts.meicanRequiredDesc }}</div>
      <button class="primary-button" @click="openPreferencePage()">{{ texts.preferenceAction }}</button>
    </div>

    <template v-else>
      <div class="panel-title">{{ texts.weekRecommendTitle }}</div>

      <div class="empty-card" v-if="!hasRecommendUser">
        <div class="empty-desc">{{ texts.recommendNeedIdentity }}</div>
      </div>

      <div class="empty-card" v-else-if="!recommendWeek.length && !loading">
        <div class="empty-desc">{{ texts.weekRecommendEmpty }}</div>
      </div>

      <template v-else>
        <div class="date-hint">{{ texts.dateSwitchHint }}</div>
        <div class="date-scroll">
          <div class="date-list">
            <button
              v-for="day in recommendWeek"
              :key="day.dateKey"
              class="date-chip"
              :class="{ 'date-chip-active': day.dateKey === selectedRecommendDate }"
              @click="
                selectedRecommendDate = day.dateKey;
                updateSelectedDay();
              "
            >
              <div class="date-chip-top">{{ day.dateLabel }}</div>
              <div class="date-chip-bottom">{{ day.weekLabel }}</div>
            </button>
          </div>
        </div>

        <div class="day-card">
          <div class="day-head">
            <span class="day-date">{{ recommendDayTitle }}</span>
            <span v-if="recommendDayIsToday" class="day-badge">{{ texts.timelineToday }}</span>
          </div>

          <div class="subsection-title">{{ texts.lunchSlot }}</div>
          <template v-if="lunchRecsShown.length">
            <div v-for="row in lunchRecsShown" :key="`l-${row.id}`" class="menu-item">
              <div class="menu-item-top">
                <div class="menu-main">
                  <div class="menu-name-row">
                    <span v-if="row.rankNo" class="rank-pill">{{ row.rankNo }}</span>
                    <span class="menu-name">{{ row.name }}</span>
                  </div>
                  <div v-if="row.restaurant" class="menu-sub">{{ row.restaurant }}</div>
                  <div v-if="row.reason" class="menu-sub muted">{{ row.reason }}</div>
                </div>
                <div class="menu-side">
                  <div v-if="row.price" class="menu-price">¥{{ row.price }}</div>
                  <div class="menu-status">{{ row.statusText }}</div>
                </div>
              </div>

              <div class="rec-order-row">
                <button
                  v-if="row.orderBtn === 'ordered'"
                  class="rec-order-pill rec-order-pill-cancel"
                  :disabled="loading"
                  @click="handleRecommendOrderTap('LUNCH', 'cancel', row)"
                >
                  {{ texts.recommendCancelOrderBtn }}
                </button>
                <button
                  v-else-if="row.orderBtn === 'place'"
                  class="rec-order-pill rec-order-pill-primary"
                  :disabled="loading"
                  @click="handleRecommendOrderTap('LUNCH', 'place', row)"
                >
                  {{ texts.recommendOrderBtn }}
                </button>
                <button
                  v-else
                  class="rec-order-pill rec-order-pill-change"
                  :disabled="loading"
                  @click="handleRecommendOrderTap('LUNCH', 'change', row)"
                >
                  {{ texts.recommendChangeOrderBtn }}
                </button>
              </div>
            </div>
          </template>
          <div v-else class="placeholder-row">{{ texts.recommendSlotEmpty }}</div>

          <div class="subsection-title">{{ texts.dinnerSlot }}</div>
          <template v-if="dinnerRecsShown.length">
            <div v-for="row in dinnerRecsShown" :key="`d-${row.id}`" class="menu-item">
              <div class="menu-item-top">
                <div class="menu-main">
                  <div class="menu-name-row">
                    <span v-if="row.rankNo" class="rank-pill">{{ row.rankNo }}</span>
                    <span class="menu-name">{{ row.name }}</span>
                  </div>
                  <div v-if="row.restaurant" class="menu-sub">{{ row.restaurant }}</div>
                  <div v-if="row.reason" class="menu-sub muted">{{ row.reason }}</div>
                </div>
                <div class="menu-side">
                  <div v-if="row.price" class="menu-price">¥{{ row.price }}</div>
                  <div class="menu-status">{{ row.statusText }}</div>
                </div>
              </div>

              <div class="rec-order-row">
                <button
                  v-if="row.orderBtn === 'ordered'"
                  class="rec-order-pill rec-order-pill-cancel"
                  :disabled="loading"
                  @click="handleRecommendOrderTap('DINNER', 'cancel', row)"
                >
                  {{ texts.recommendCancelOrderBtn }}
                </button>
                <button
                  v-else-if="row.orderBtn === 'place'"
                  class="rec-order-pill rec-order-pill-primary"
                  :disabled="loading"
                  @click="handleRecommendOrderTap('DINNER', 'place', row)"
                >
                  {{ texts.recommendOrderBtn }}
                </button>
                <button
                  v-else
                  class="rec-order-pill rec-order-pill-change"
                  :disabled="loading"
                  @click="handleRecommendOrderTap('DINNER', 'change', row)"
                >
                  {{ texts.recommendChangeOrderBtn }}
                </button>
              </div>
            </div>
          </template>
          <div v-else class="placeholder-row">{{ texts.recommendSlotEmpty }}</div>
        </div>
      </template>
    </template>

    <div v-if="error" class="error-card">{{ error }}</div>

    <div v-if="addressSheetOpen" class="sheet-mask" @click.self="closeAddressSheet(null)">
      <div class="sheet-panel">
        <div class="sheet-head">
          <div class="sheet-title">{{ addressSheetTitle }}</div>
          <button class="sheet-close" @click="closeAddressSheet(null)">关闭</button>
        </div>
        <div class="sheet-list">
          <button
            v-for="(item, index) in addressSheetOptions"
            :key="`${item.corpAddressUniqueId}-${index}`"
            class="sheet-item"
            @click="closeAddressSheet(item)"
          >
            {{ index + 1 }}. {{ item.label || item.corpAddressUniqueId }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.top-card,
.auto-order-card,
.empty-card,
.day-card,
.error-card {
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 16px 40px rgba(82, 56, 28, 0.06);
}

.top-card {
  padding: 28px 26px;
}

.status-row,
.lang-row,
.day-head,
.menu-item-top,
.auto-order-row,
.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.status-label {
  color: #8d775f;
  font-size: 14px;
}

.status-value {
  color: #d66a20;
  font-size: 18px;
  font-weight: 600;
}

.status-value.status-on {
  color: #2b8f47;
}

.lang-row {
  gap: 12px;
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid rgba(47, 36, 24, 0.08);
}

.lang-picker-trigger {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px;
  border-radius: 999px;
  background: rgba(47, 36, 24, 0.07);
  color: #302417;
  min-width: 148px;
}

.lang-select {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.lang-picker-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 15px;
  font-weight: 500;
}

.lang-picker-caret {
  flex-shrink: 0;
  font-size: 10px;
  color: #8d775f;
}

.pref-entry,
.primary-button {
  margin-top: 22px;
  border: none;
  border-radius: 999px;
  background: #2f2418;
  color: #fff6ea;
  font-size: 16px;
  font-weight: 600;
  padding: 14px 20px;
  cursor: pointer;
}

.pref-entry {
  width: 100%;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.tag-item {
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  color: #8f5a21;
  font-size: 13px;
}

.auto-order-card,
.empty-card,
.error-card {
  margin-top: 18px;
  padding: 24px;
}

.auto-order-copy {
  flex: 1;
  min-width: 0;
}

.auto-order-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.auto-order-title {
  color: #2f2418;
  font-size: 20px;
  font-weight: 600;
}

.auto-order-tip-icon {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 50%;
  background: rgba(47, 36, 24, 0.12);
  color: #5f4a32;
  font-weight: 700;
  cursor: pointer;
}

.auto-order-desc,
.empty-desc {
  margin-top: 10px;
  color: #7f6850;
  font-size: 14px;
  line-height: 1.6;
}

.empty-title {
  color: #302417;
  font-size: 22px;
  font-weight: 600;
}

.switch-toggle {
  position: relative;
  display: inline-flex;
  align-items: center;
  width: 54px;
  height: 32px;
}

.switch-toggle input {
  position: absolute;
  opacity: 0;
}

.switch-slider {
  width: 54px;
  height: 32px;
  border-radius: 999px;
  background: #e5ddd0;
  transition: background 0.2s ease;
  position: relative;
}

.switch-slider::after {
  content: '';
  position: absolute;
  top: 4px;
  left: 4px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.2s ease;
}

.switch-toggle input:checked + .switch-slider {
  background: #22c55e;
}

.switch-toggle input:checked + .switch-slider::after {
  transform: translateX(22px);
}

.panel-title {
  margin: 28px 4px 14px;
  color: #302417;
  font-size: 20px;
  font-weight: 600;
}

.date-hint {
  margin: 0 4px 14px;
  color: #8d775f;
  font-size: 13px;
}

.date-scroll {
  overflow-x: auto;
  margin-bottom: 18px;
}

.date-list {
  display: inline-flex;
  gap: 12px;
  padding-bottom: 6px;
}

.date-chip {
  min-width: 104px;
  padding: 16px;
  border: none;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 10px 24px rgba(82, 56, 28, 0.05);
  text-align: center;
  cursor: pointer;
}

.date-chip-active {
  background: #2f2418;
}

.date-chip-top {
  color: #302417;
  font-size: 15px;
  font-weight: 600;
}

.date-chip-bottom {
  margin-top: 8px;
  color: #8d775f;
  font-size: 13px;
}

.date-chip-active .date-chip-top,
.date-chip-active .date-chip-bottom {
  color: #fff6ea;
}

.day-card {
  padding: 24px;
  margin-bottom: 18px;
}

.day-date {
  color: #302417;
  font-size: 18px;
  font-weight: 600;
}

.day-badge {
  padding: 5px 12px;
  border-radius: 999px;
  background: #fff2e8;
  color: #d6651c;
  font-size: 12px;
}

.subsection-title {
  margin-top: 22px;
  color: #8c5d24;
  font-size: 15px;
  font-weight: 600;
}

.menu-item {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px 0;
  border-bottom: 1px solid #f0e5d9;
}

.menu-item:last-child {
  border-bottom: none;
}

.menu-main {
  flex: 1;
  min-width: 0;
}

.menu-name-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.rank-pill {
  min-width: 28px;
  padding: 3px 8px;
  border-radius: 10px;
  background: rgba(47, 36, 24, 0.08);
  color: #5f4a32;
  font-size: 12px;
  font-weight: 600;
  text-align: center;
}

.menu-name {
  color: #302417;
  font-size: 17px;
  font-weight: 600;
  line-height: 1.4;
}

.menu-sub {
  margin-top: 8px;
  color: #756451;
  font-size: 13px;
  line-height: 1.5;
}

.menu-sub.muted {
  color: #9a8b7a;
}

.menu-side {
  flex-shrink: 0;
  text-align: right;
}

.menu-price {
  color: #302417;
  font-size: 16px;
  font-weight: 600;
}

.menu-status {
  margin-top: 8px;
  color: #8c5d24;
  font-size: 12px;
}

.rec-order-row {
  display: flex;
  justify-content: flex-end;
}

.rec-order-pill {
  border: none;
  border-radius: 999px;
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.rec-order-pill-primary {
  background: #2f2418;
  color: #fff6ea;
}

.rec-order-pill-change {
  background: #fff0df;
  color: #9a5c1e;
}

.rec-order-pill-cancel {
  background: rgba(188, 64, 64, 0.12);
  color: #b13d3d;
}

.placeholder-row {
  padding: 20px 0;
  color: #8d775f;
  font-size: 14px;
}

.error-card {
  color: #b23c1e;
  font-size: 14px;
}

.sheet-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.42);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 12px;
  z-index: 70;
}

.sheet-panel {
  width: min(720px, 100%);
  border-radius: 24px;
  background: #fff;
  padding: 18px;
}

.sheet-title {
  font-size: 18px;
  font-weight: 600;
}

.sheet-close,
.sheet-item {
  border: none;
  cursor: pointer;
}

.sheet-close {
  background: rgba(47, 36, 24, 0.08);
  color: #5f4a32;
  border-radius: 999px;
  padding: 8px 14px;
}

.sheet-list {
  margin-top: 14px;
  display: grid;
  gap: 10px;
}

.sheet-item {
  width: 100%;
  text-align: left;
  padding: 14px 16px;
  border-radius: 18px;
  background: #faf5ee;
  color: #2f2418;
}
</style>
