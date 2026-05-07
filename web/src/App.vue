<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { currentLanguage } from './shared/dietProfile';
import { getTabBarTexts } from './shared/i18n';
import { toastMessage } from './shared/toast';

const route = useRoute();
const router = useRouter();

const tabs = computed(() => getTabBarTexts(currentLanguage.value));
const showTabbar = computed(() => ['/home', '/menu', '/mine'].includes(route.path));
</script>

<template>
  <div class="app-shell">
    <div class="app-content">
      <router-view />
    </div>

    <nav v-if="showTabbar" class="tabbar">
      <button
        v-for="tab in tabs"
        :key="tab.path"
        class="tabbar-item"
        :class="{ active: route.path === tab.path }"
        @click="router.push(tab.path)"
      >
        <span class="tabbar-label">{{ tab.text }}</span>
      </button>
    </nav>

    <transition name="toast-fade">
      <div v-if="toastMessage" class="app-toast">{{ toastMessage }}</div>
    </transition>
  </div>
</template>
