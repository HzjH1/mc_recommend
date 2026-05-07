<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { getWeeklyMenus, type WeeklyMenuSection } from '../api';
import { currentLanguage, profileState } from '../shared/dietProfile';
import { getTranslations } from '../shared/i18n';
import { sessionState } from '../shared/meicanSession';
import { stableRecommendUserId } from '../shared/recommendUserId';

const loading = ref(false);
const error = ref('');
const weekDates = ref<Array<{ dateKey: string; label: string; weekLabel: string; isToday?: boolean }>>([]);
const selectedDate = ref('');
const mealSections = ref<WeeklyMenuSection[]>([]);

const texts = computed(() => getTranslations(currentLanguage.value).meican);
const profile = computed(() => profileState.value);
const session = computed(() => sessionState.value);
const meicanLoggedIn = computed(() => !!(session.value.accessToken && session.value.phone === profile.value.phone));
const recommendUserId = computed(() => stableRecommendUserId());

function activeNamespace() {
  if (`${profile.value.meicanCorpNamespace || ''}`.trim()) {
    return `${profile.value.meicanCorpNamespace || ''}`.trim();
  }
  return `${session.value.accountNamespace || ''}`.trim();
}

function normalizeMealSections(sections: WeeklyMenuSection[] = []) {
  return sections.map((section) => ({
    ...section,
    restaurants: (section.restaurants || []).map((restaurant) => ({
      ...restaurant,
      distanceText: restaurant.distance ? `${restaurant.distance}${texts.value.distanceUnit}` : '',
      statusText: restaurant.status === 'sold_out' ? texts.value.soldOut : texts.value.available,
      menus: (restaurant.menus || []).map((menu) => ({
        ...menu,
        statusText: menu.status === 'sold_out' ? texts.value.soldOut : texts.value.available,
      })),
    })),
    recommendedDishes: (section.recommendedDishes || []).map((dish) => ({
      ...dish,
      statusText: dish.status === 'sold_out' ? texts.value.soldOut : texts.value.available,
    })),
  }));
}

async function loadPageBundle(sync = false) {
  error.value = '';

  if (!meicanLoggedIn.value || !recommendUserId.value) {
    weekDates.value = [];
    selectedDate.value = '';
    mealSections.value = [];
    return;
  }

  loading.value = true;

  try {
    const data = await getWeeklyMenus(recommendUserId.value, {
      date: selectedDate.value || undefined,
      namespace: activeNamespace() || undefined,
      sync,
    });
    weekDates.value = data.weekDates || [];
    selectedDate.value = data.selectedDate || '';
    mealSections.value = normalizeMealSections(data.mealSections || []);
  } catch (err: any) {
    error.value = String(err?.message || err);
    weekDates.value = [];
    selectedDate.value = '';
    mealSections.value = [];
  } finally {
    loading.value = false;
  }
}

async function handleDateSelect(dateKey: string) {
  if (!dateKey || dateKey === selectedDate.value) {
    return;
  }
  selectedDate.value = dateKey;
  await loadPageBundle(false);
}

onMounted(() => {
  loadPageBundle(true);
});
</script>

<template>
  <div class="page-shell meican-page">
    <div class="hero-card">
      <div class="hero-title">{{ texts.heroTitle }}</div>
      <div class="hero-desc">{{ texts.heroDesc }}</div>
      <button class="hero-action" :disabled="loading" @click="loadPageBundle(true)">{{ texts.refreshAction }}</button>
    </div>

    <div v-if="!meicanLoggedIn || !recommendUserId" class="empty-card">
      <div class="empty-title">{{ texts.emptyTitle }}</div>
      <div class="empty-desc">{{ texts.emptyDesc }}</div>
    </div>

    <template v-else>
      <div class="panel-title">{{ texts.weeklyMenuTitle }}</div>
      <div class="date-hint">{{ texts.dateSwitcherHint }}</div>

      <div class="date-scroll">
        <div class="date-list">
          <button
            v-for="day in weekDates"
            :key="day.dateKey"
            class="date-chip"
            :class="{ 'date-chip-active': day.dateKey === selectedDate }"
            @click="handleDateSelect(day.dateKey)"
          >
            <div class="date-chip-top">{{ day.label }}</div>
            <div class="date-chip-bottom">{{ day.weekLabel }}</div>
          </button>
        </div>
      </div>

      <div v-if="!mealSections.length && !loading" class="empty-card">
        <div class="empty-title">{{ texts.noMenus }}</div>
        <div class="empty-desc">{{ texts.emptyDesc }}</div>
      </div>

      <div v-if="error" class="error-card">{{ error }}</div>

      <div class="restaurant-list" v-if="mealSections.length">
        <div v-for="section in mealSections" :key="section.key" class="restaurant-card">
          <div class="section-head">{{ section.title }}</div>

          <div v-if="section.restaurants.length" class="subsection-title">{{ texts.menuTitle }}</div>
          <div v-for="restaurant in section.restaurants" :key="restaurant.id" class="menu-block">
            <div class="menu-block-title">{{ restaurant.name }}</div>
            <div v-for="menu in restaurant.menus" :key="menu.id" class="menu-item">
              <div class="menu-main">
                <div class="menu-name">{{ menu.name }}</div>
              </div>
              <div class="menu-side">
                <div v-if="menu.price" class="menu-price">¥{{ menu.price }}</div>
                <div class="menu-status" :class="{ 'menu-status-off': menu.status === 'sold_out' }">
                  {{ menu.statusText }}
                </div>
              </div>
            </div>
          </div>

          <div v-if="section.recommendedDishes.length" class="subsection-title">{{ texts.recommendedTitle }}</div>
          <div v-for="dish in section.recommendedDishes" :key="dish.id" class="menu-item">
            <div class="menu-main">
              <div class="menu-name">{{ dish.name }}</div>
            </div>
            <div class="menu-side">
              <div v-if="dish.price" class="menu-price">¥{{ dish.price }}</div>
              <div class="menu-status" :class="{ 'menu-status-off': dish.status === 'sold_out' }">
                {{ dish.statusText }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.meican-page {
  background:
    radial-gradient(circle at top right, rgba(255, 215, 170, 0.35), transparent 32%),
    linear-gradient(180deg, #fff8ef 0%, #f6f1e8 100%);
}

.hero-card,
.restaurant-card,
.empty-card,
.error-card {
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 16px 40px rgba(82, 56, 28, 0.06);
}

.hero-card {
  padding: 30px;
}

.hero-title {
  color: #2f2418;
  font-size: 32px;
  font-weight: 600;
}

.hero-desc {
  margin-top: 12px;
  color: #756451;
  font-size: 15px;
  line-height: 1.7;
}

.hero-action {
  display: inline-flex;
  margin-top: 22px;
  padding: 12px 20px;
  border: none;
  border-radius: 999px;
  background: #2f2418;
  color: #fff6ea;
  font-size: 14px;
  cursor: pointer;
}

.empty-card,
.error-card {
  margin-top: 24px;
  padding: 30px;
}

.empty-title {
  color: #302417;
  font-size: 22px;
  font-weight: 600;
}

.empty-desc {
  margin-top: 12px;
  color: #756451;
  font-size: 15px;
  line-height: 1.7;
}

.error-card {
  color: #b23c1e;
  font-size: 14px;
}

.restaurant-list {
  margin-top: 24px;
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

.restaurant-card {
  padding: 28px;
  margin-bottom: 18px;
}

.section-head {
  color: #302417;
  font-size: 20px;
  font-weight: 600;
}

.subsection-title {
  margin-top: 24px;
  color: #8c5d24;
  font-size: 15px;
  font-weight: 600;
}

.menu-block {
  margin-top: 12px;
}

.menu-block-title {
  color: #5b4833;
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 6px;
}

.menu-item {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 0;
  border-bottom: 1px solid #f0e5d9;
}

.menu-item:last-child {
  border-bottom: none;
}

.menu-main {
  flex: 1;
}

.menu-name {
  color: #302417;
  font-size: 17px;
  font-weight: 500;
}

.menu-side {
  text-align: right;
}

.menu-price {
  color: #b35f1f;
  font-size: 17px;
  font-weight: 600;
}

.menu-status {
  margin-top: 10px;
  color: #2e8b57;
  font-size: 12px;
}

.menu-status-off {
  color: #c06b5a;
}
</style>
