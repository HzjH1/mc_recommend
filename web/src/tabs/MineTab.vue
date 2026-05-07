<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { getMaskedPhone, getPreferenceTags, hasProfile, profileState } from '../shared/dietProfile';
import { getTranslations, type Language } from '../shared/i18n';

const router = useRouter();

const profile = computed(() => profileState.value);
const texts = computed(() => getTranslations((profile.value.language || 'zh-CN') as Language).me);
const avatarText = computed(() => profile.value.avatarText || '食');
const maskedPhone = computed(() => getMaskedPhone(profile.value));
const preferenceTags = computed(() => (hasProfile.value ? getPreferenceTags(profile.value) : []));

function openPreferencePage() {
  router.push('/preferences');
}

function handleMenuTap(type: 'feedback' | 'preferences') {
  router.push(type === 'feedback' ? '/feedback' : '/preferences');
}
</script>

<template>
  <div class="page-shell me-page">
    <div class="profile-card">
      <div class="avatar">{{ avatarText }}</div>

      <div class="profile-info">
        <div class="profile-title">{{ texts.userTitle }}</div>
        <div class="profile-phone">{{ maskedPhone }}</div>
      </div>

      <button class="profile-action" @click="openPreferencePage">
        {{ hasProfile ? texts.editAction : texts.setupNow }}
      </button>
    </div>

    <div class="panel-card">
      <div class="panel-title">{{ texts.preferenceTitle }}</div>

      <div v-if="preferenceTags.length" class="tag-list">
        <div v-for="tag in preferenceTags" :key="tag" class="tag-item">{{ tag }}</div>
      </div>

      <div v-else class="panel-empty">{{ texts.preferenceEmpty }}</div>
    </div>

    <div class="menu-group">
      <button class="menu-item" @click="handleMenuTap('feedback')">
        <div class="menu-copy">
          <div class="menu-title">{{ texts.feedbackTitle }}</div>
          <div class="menu-desc">{{ texts.feedbackDesc }}</div>
        </div>
        <div class="menu-value">{{ texts.feedbackAction }}</div>
      </button>

      <button class="menu-item" @click="handleMenuTap('preferences')">
        <div class="menu-copy">
          <div class="menu-title">{{ texts.preferenceEntryTitle }}</div>
          <div class="menu-desc">{{ texts.preferenceEntryDesc }}</div>
        </div>
        <div class="menu-value">{{ hasProfile ? texts.editAction : texts.fillAction }}</div>
      </button>
    </div>
  </div>
</template>

<style scoped>
.me-page {
  background:
    radial-gradient(circle at top left, rgba(255, 224, 171, 0.35), transparent 34%),
    linear-gradient(180deg, #fff9ef 0%, #f5efe5 100%);
}

.profile-card,
.panel-card,
.menu-group {
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 16px 40px rgba(83, 58, 30, 0.06);
}

.profile-card {
  display: flex;
  align-items: center;
  padding: 30px;
}

.avatar {
  width: 88px;
  height: 88px;
  border-radius: 50%;
  background: linear-gradient(135deg, #ff9f57 0%, #ff7541 100%);
  color: #fff;
  font-size: 34px;
  font-weight: 600;
  line-height: 88px;
  text-align: center;
}

.profile-info {
  flex: 1;
  margin-left: 20px;
}

.profile-title {
  color: #302417;
  font-size: 28px;
  font-weight: 600;
}

.profile-phone {
  margin-top: 10px;
  color: #7c6a56;
  font-size: 14px;
}

.profile-action {
  border: none;
  padding: 12px 18px;
  border-radius: 999px;
  background: #2f2418;
  color: #fff4e8;
  font-size: 14px;
  cursor: pointer;
}

.panel-card {
  margin-top: 22px;
  padding: 28px;
}

.panel-title {
  color: #302417;
  font-size: 20px;
  font-weight: 600;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 18px;
}

.tag-item {
  padding: 10px 18px;
  border-radius: 999px;
  background: #fff2d8;
  color: #8b5c26;
  font-size: 13px;
}

.panel-empty {
  margin-top: 18px;
  color: #7c6a56;
  font-size: 15px;
  line-height: 1.6;
}

.menu-group {
  margin-top: 22px;
  overflow: hidden;
}

.menu-item {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding: 28px;
  border: none;
  border-bottom: 1px solid #f0e5d8;
  background: transparent;
  cursor: pointer;
}

.menu-item:last-child {
  border-bottom: none;
}

.menu-copy {
  flex: 1;
  text-align: left;
}

.menu-title {
  color: #302417;
  font-size: 18px;
  font-weight: 600;
}

.menu-desc {
  margin-top: 10px;
  color: #8f7c67;
  font-size: 13px;
}

.menu-value {
  color: #b56b22;
  font-size: 14px;
}
</style>
