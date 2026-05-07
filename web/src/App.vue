<script setup lang="ts">
import { computed, ref } from 'vue';
import {
  getDailyRecommendations,
  getUserOrderAddresses,
  postManualOrder,
  postManualOrderCancel,
  type AddressOption,
  type DailyRecommendations,
} from './api';

const apiBase = ref(localStorage.getItem('mc_api_base') || '');
const userId = ref(localStorage.getItem('mc_user_id') || '');
const namespace = ref(localStorage.getItem('mc_namespace') || '');
const date = ref(new Date().toISOString().slice(0, 10));

const loading = ref(false);
const err = ref('');
const data = ref<DailyRecommendations | null>(null);

const lunchAddr = ref<{ options: AddressOption[]; selectedCorpAddressId: string } | null>(null);
const dinnerAddr = ref<{ options: AddressOption[]; selectedCorpAddressId: string } | null>(null);

function persist() {
  localStorage.setItem('mc_api_base', apiBase.value);
  localStorage.setItem('mc_user_id', userId.value);
  localStorage.setItem('mc_namespace', namespace.value);
}

const hasBase = computed(() => !!apiBase.value.trim());
const hasUser = computed(() => !!userId.value.trim());

async function loadAll() {
  err.value = '';
  persist();
  if (!hasBase.value) return (err.value = '请先填写 apiBase');
  if (!hasUser.value) return (err.value = '请先填写 userId');
  loading.value = true;
  try {
    data.value = await getDailyRecommendations(apiBase.value, userId.value, date.value, namespace.value || undefined);
    lunchAddr.value = await getUserOrderAddresses(apiBase.value, userId.value, namespace.value || undefined, 'LUNCH');
    dinnerAddr.value = await getUserOrderAddresses(apiBase.value, userId.value, namespace.value || undefined, 'DINNER');
  } catch (e: any) {
    err.value = String(e?.message || e);
  } finally {
    loading.value = false;
  }
}

function makeIdemKey(slot: string, menuItemId: number) {
  return `web:${userId.value}:${date.value}:${slot}:${menuItemId}:${Date.now()}`;
}

async function doOrder(slot: 'LUNCH' | 'DINNER', menuItemId: number) {
  err.value = '';
  persist();
  loading.value = true;
  try {
    const addr = slot === 'LUNCH' ? lunchAddr.value : dinnerAddr.value;
    const defaultCorpAddressId = addr?.selectedCorpAddressId || '';
    await postManualOrder(apiBase.value, userId.value, {
      date: date.value,
      mealSlot: slot,
      namespace: namespace.value || undefined,
      menuItemId,
      idempotencyKey: makeIdemKey(slot, menuItemId),
      replace: true,
      defaultCorpAddressId: defaultCorpAddressId || undefined,
    });
    await loadAll();
  } catch (e: any) {
    err.value = String(e?.message || e);
  } finally {
    loading.value = false;
  }
}

async function doCancel(slot: 'LUNCH' | 'DINNER') {
  err.value = '';
  persist();
  loading.value = true;
  try {
    await postManualOrderCancel(apiBase.value, userId.value, { date: date.value, mealSlot: slot });
    await loadAll();
  } catch (e: any) {
    err.value = String(e?.message || e);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="container">
    <div class="card">
      <h3 style="margin: 0 0 10px 0">mc_recommend Web</h3>
      <div class="row">
        <div>
          <label>apiBase</label>
          <input v-model="apiBase" placeholder="https://你的后端域名" />
          <div class="muted">示例：来自 mc1 的 recommendConfig.apiBase</div>
        </div>
        <div>
          <label>userId</label>
          <input v-model="userId" placeholder="用户ID（数字）" />
        </div>
        <div>
          <label>namespace（可选）</label>
          <input v-model="namespace" placeholder="例如 915402" />
        </div>
        <div>
          <label>date</label>
          <input v-model="date" type="date" />
        </div>
        <div style="display:flex; align-items:end; gap:10px">
          <button @click="loadAll" :disabled="loading">加载</button>
          <button class="secondary" @click="doCancel('LUNCH')" :disabled="loading">取消午餐</button>
          <button class="secondary" @click="doCancel('DINNER')" :disabled="loading">取消晚餐</button>
        </div>
      </div>
      <div v-if="err" class="err" style="margin-top: 10px">{{ err }}</div>
      <div v-if="loading" class="muted" style="margin-top: 10px">加载中…</div>
    </div>

    <div v-if="data" class="row" style="margin-top: 16px">
      <div class="card" style="flex:1; min-width: 360px">
        <h3 style="margin-top: 0">午餐（{{ data.date }}）</h3>
        <div class="muted" v-if="lunchAddr">
          默认地址：{{ lunchAddr.selectedCorpAddressId || '未设置（将走后端兜底地址解析）' }}
        </div>
        <div class="list" style="margin-top: 12px">
          <div class="item" v-for="x in data.LUNCH" :key="x.menuItemId">
            <h4>#{{ x.rankNo }} {{ x.dishName }}</h4>
            <div class="meta">
              <span>餐厅：{{ x.restaurantName }}</span>
              <span>价格：{{ (x.priceCent / 100).toFixed(2) }}</span>
              <span>score：{{ x.score }}</span>
              <span>已下单：{{ x.ordered ? '是' : '否' }}</span>
            </div>
            <div class="muted" style="margin-top: 6px">理由：{{ x.reason }}</div>
            <div style="margin-top:10px">
              <button @click="doOrder('LUNCH', x.menuItemId)" :disabled="loading">用该菜下单（replace）</button>
            </div>
          </div>
          <div class="muted" v-if="data.LUNCH.length === 0">无午餐推荐</div>
        </div>
      </div>

      <div class="card" style="flex:1; min-width: 360px">
        <h3 style="margin-top: 0">晚餐（{{ data.date }}）</h3>
        <div class="muted" v-if="dinnerAddr">
          默认地址：{{ dinnerAddr.selectedCorpAddressId || '未设置（将走后端兜底地址解析）' }}
        </div>
        <div class="list" style="margin-top: 12px">
          <div class="item" v-for="x in data.DINNER" :key="x.menuItemId">
            <h4>#{{ x.rankNo }} {{ x.dishName }}</h4>
            <div class="meta">
              <span>餐厅：{{ x.restaurantName }}</span>
              <span>价格：{{ (x.priceCent / 100).toFixed(2) }}</span>
              <span>score：{{ x.score }}</span>
              <span>已下单：{{ x.ordered ? '是' : '否' }}</span>
            </div>
            <div class="muted" style="margin-top: 6px">理由：{{ x.reason }}</div>
            <div style="margin-top:10px">
              <button @click="doOrder('DINNER', x.menuItemId)" :disabled="loading">用该菜下单（replace）</button>
            </div>
          </div>
          <div class="muted" v-if="data.DINNER.length === 0">无晚餐推荐</div>
        </div>
      </div>
    </div>
  </div>
</template>

