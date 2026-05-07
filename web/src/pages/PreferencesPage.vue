<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { putUserPreferences } from '../api';
import {
  DEFAULT_DIET_PROFILE,
  derivePrimaryStaple,
  normalizeStaplePreferences,
  profileState,
  saveDietProfile,
  STAPLE_PREFERENCE_VALUES,
  type DietProfile,
} from '../shared/dietProfile';
import { getTranslations } from '../shared/i18n';
import { clearMeicanSession, sessionState } from '../shared/meicanSession';
import { sendPhoneVerificationCode, syncMeicanProfileByPhone } from '../shared/mockMeicanAuth';
import { stableRecommendUserId } from '../shared/recommendUserId';
import { showToast } from '../shared/toast';
import { phoneRegCheck } from '../shared/util';

const route = useRoute();
const router = useRouter();

const saving = ref(false);
const codeSending = ref(false);
const verificationCode = ref('');

const form = reactive<DietProfile>({
  ...DEFAULT_DIET_PROFILE,
});

const texts = computed(() => getTranslations(form.language).preference);
const commonTexts = computed(() => getTranslations(form.language).common);
const isOnboarding = computed(() => route.query.mode === 'onboarding' || !profileState.value.phone);
const submitLabel = computed(() => (isOnboarding.value ? texts.value.submitOnboarding : texts.value.submitEdit));
const meicanLoggedIn = computed(() => !!(sessionState.value.accessToken && sessionState.value.phone === form.phone.trim()));
const stapleOptions = computed(() =>
  STAPLE_PREFERENCE_VALUES.map((value) => ({
    value,
    label: texts.value[value],
    active: (form.staplePreferences || []).includes(value),
  })),
);
const otherStapleSelected = computed(() => (form.staplePreferences || []).includes('other'));

function applyProfileToForm() {
  Object.assign(form, {
    ...DEFAULT_DIET_PROFILE,
    ...profileState.value,
    staplePreferences: normalizeStaplePreferences(profileState.value.staplePreferences, profileState.value.staple),
  });
}

function buildPreferencePayload(profile: DietProfile) {
  const staplePreferences = normalizeStaplePreferences(profile.staplePreferences, profile.staple);
  const otherNotes = `${profile.otherNotes || ''}`.trim();

  return {
    prefersSpicy: !!profile.prefersSpicy,
    isHalal: !!profile.isHalal,
    isCutting: !!profile.isCutting,
    staple: derivePrimaryStaple(staplePreferences, profile.staple),
    staplePreferences,
    preferNoodle: staplePreferences.includes('noodle'),
    preferRice: staplePreferences.includes('rice'),
    preferBurger: staplePreferences.includes('burger'),
    other: otherNotes,
    taboo: otherNotes,
  };
}

async function syncRecommendPreferences(profile: DietProfile) {
  const userId = stableRecommendUserId();
  if (!userId) {
    return;
  }

  try {
    await putUserPreferences(userId, buildPreferencePayload(profile));
  } catch {
    // 本地资料保存成功时不阻断流程
  }
}

function toggleStapleOption(value: string) {
  const current = new Set(form.staplePreferences || []);
  if (current.has(value as any)) {
    if (current.size === 1) {
      return;
    }
    current.delete(value as any);
  } else {
    current.add(value as any);
  }
  form.staplePreferences = STAPLE_PREFERENCE_VALUES.filter((item) => current.has(item));
}

async function handleSendCode() {
  const phone = `${form.phone || ''}`.trim();
  if (!phoneRegCheck(phone)) {
    showToast(commonTexts.value.profileRequired);
    return;
  }

  codeSending.value = true;
  try {
    await sendPhoneVerificationCode(phone);
    showToast(commonTexts.value.sendCodeSuccess);
  } catch {
    showToast(commonTexts.value.sendCodeFailed);
  } finally {
    codeSending.value = false;
  }
}

async function submitForm() {
  const storedProfile = profileState.value || DEFAULT_DIET_PROFILE;
  const phone = `${form.phone || ''}`.trim();
  const otherNotes = `${form.otherNotes || ''}`.trim();
  const staplePreferences = normalizeStaplePreferences(form.staplePreferences, form.staple);
  const phoneChanged = !!storedProfile.phone && storedProfile.phone !== phone;

  if (!phoneRegCheck(phone)) {
    showToast(commonTexts.value.profileRequired);
    return;
  }

  if (staplePreferences.includes('other') && !otherNotes) {
    showToast(texts.value.otherInputRequired);
    return;
  }

  saving.value = true;

  try {
    const profileSeed = phoneChanged
      ? {
          ...storedProfile,
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
        }
      : storedProfile;

    if (phoneChanged) {
      clearMeicanSession();
    }

    let nextProfile = saveDietProfile({
      ...profileSeed,
      ...form,
      phone,
      otherNotes,
      taboo: otherNotes,
      staplePreferences,
      staple: derivePrimaryStaple(staplePreferences, form.staple),
      language: form.language,
    });

    Object.assign(form, nextProfile);
    await syncRecommendPreferences(nextProfile);

    if (`${verificationCode.value || ''}`.trim()) {
      nextProfile = await syncMeicanProfileByPhone({
        phone,
        verificationCode: `${verificationCode.value || ''}`.trim(),
      });
      Object.assign(form, nextProfile);
      await syncRecommendPreferences(nextProfile);
      verificationCode.value = '';
      showToast(commonTexts.value.meicanLoginSuccess);
    } else {
      showToast(commonTexts.value.saveSuccess);
    }

    window.setTimeout(() => {
      if (window.history.length > 1) {
        router.back();
        return;
      }
      router.replace('/home');
    }, 260);
  } catch {
    showToast(commonTexts.value.meicanLoginFailed);
    window.setTimeout(() => {
      if (window.history.length > 1) {
        router.back();
        return;
      }
      router.replace('/home');
    }, 260);
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  applyProfileToForm();
});
</script>

<template>
  <div class="page-shell preferences-page">
    <div class="hero-card">
      <div class="hero-glow hero-glow-one"></div>
      <div class="hero-glow hero-glow-two"></div>

      <div class="hero-tag">{{ texts.heroTag }}</div>
      <div class="hero-title">{{ texts.heroTitle }}</div>
      <div class="hero-desc">{{ isOnboarding ? texts.introDesc : texts.heroDesc }}</div>

      <div class="hero-status-row">
        <div class="hero-status" :class="{ 'hero-status-bound': meicanLoggedIn }">
          <div class="hero-status-dot"></div>
          <span>{{ meicanLoggedIn ? texts.loginStatusBound : texts.loginStatusUnbound }}</span>
        </div>
        <div class="hero-caption">{{ isOnboarding ? texts.introOnboarding : texts.introEdit }}</div>
      </div>
    </div>

    <div class="form-card">
      <div class="section-title">{{ texts.basicSection }}</div>

      <div class="field-block">
        <div class="field-label">{{ texts.phone }}</div>
        <input v-model="form.phone" class="text-input" maxlength="11" :placeholder="texts.phonePlaceholder" />
      </div>

      <div class="field-block">
        <div class="field-head">
          <div>
            <div class="field-label">{{ texts.meicanLoginCardTitle }}</div>
            <div class="field-helper">{{ texts.meicanLoginDesc }}</div>
          </div>
          <div class="login-status" :class="{ 'login-status-bound': meicanLoggedIn }">
            {{ meicanLoggedIn ? texts.loginStatusBound : texts.loginStatusUnbound }}
          </div>
        </div>

        <div class="code-row">
          <input v-model="verificationCode" class="text-input code-input" maxlength="8" :placeholder="texts.verificationCodePlaceholder" />
          <button class="send-code-button" :disabled="codeSending" @click="handleSendCode">
            {{ codeSending ? '...' : texts.sendCodeAction }}
          </button>
        </div>
      </div>
    </div>

    <div class="form-card">
      <div class="section-title">{{ texts.lifestyleSection }}</div>

      <div class="switch-panel">
        <div class="switch-card">
          <div class="switch-copy">
            <div class="field-label">{{ texts.spicy }}</div>
            <div class="field-helper">{{ texts.spicyDesc }}</div>
          </div>
          <input v-model="form.prefersSpicy" type="checkbox" class="switch-input" />
        </div>

        <div class="switch-card">
          <div class="switch-copy">
            <div class="field-label">{{ texts.halal }}</div>
            <div class="field-helper">{{ texts.halalDesc }}</div>
          </div>
          <input v-model="form.isHalal" type="checkbox" class="switch-input" />
        </div>

        <div class="switch-card">
          <div class="switch-copy">
            <div class="field-label">{{ texts.cutting }}</div>
            <div class="field-helper">{{ texts.cuttingDesc }}</div>
          </div>
          <input v-model="form.isCutting" type="checkbox" class="switch-input" />
        </div>
      </div>
    </div>

    <div class="form-card">
      <div class="section-title">{{ texts.staple }}</div>
      <div class="section-desc">{{ texts.stapleDesc }}</div>

      <div class="choice-grid">
        <button
          v-for="item in stapleOptions"
          :key="item.value"
          class="choice-chip"
          :class="{ 'choice-chip-active': item.active }"
          @click="toggleStapleOption(item.value)"
        >
          <span class="choice-check" :class="{ 'choice-check-active': item.active }"></span>
          <span>{{ item.label }}</span>
        </button>
      </div>
    </div>

    <div class="form-card form-card-last">
      <div class="section-title">{{ texts.otherNotes }}</div>
      <div class="section-desc">{{ texts.otherNotesDesc }}</div>
      <div v-if="otherStapleSelected && !form.otherNotes" class="highlight-tip">{{ texts.otherNotesHint }}</div>

      <textarea v-model="form.otherNotes" class="text-area" maxlength="120" :placeholder="texts.otherNotesPlaceholder"></textarea>
    </div>

    <button class="submit-button" :disabled="saving" @click="submitForm">{{ submitLabel }}</button>
  </div>
</template>

<style scoped>
.preferences-page {
  background:
    radial-gradient(circle at 12% 8%, rgba(255, 195, 110, 0.28), transparent 26%),
    radial-gradient(circle at 88% 12%, rgba(255, 138, 61, 0.18), transparent 22%),
    linear-gradient(180deg, #fff7ec 0%, #fffaf5 48%, #f5ede3 100%);
  padding-bottom: calc(env(safe-area-inset-bottom) + 48px);
}

.hero-card,
.form-card {
  position: relative;
  overflow: hidden;
  border-radius: 30px;
}

.hero-card {
  padding: 32px 30px;
  background: linear-gradient(155deg, #fff5dc 0%, #ffe9cb 48%, #fffdf8 100%);
  box-shadow: 0 22px 58px rgba(128, 86, 28, 0.12);
}

.hero-glow {
  position: absolute;
  border-radius: 50%;
}

.hero-glow-one {
  top: -50px;
  right: -30px;
  width: 180px;
  height: 180px;
  background: rgba(255, 180, 92, 0.18);
}

.hero-glow-two {
  left: 180px;
  bottom: -78px;
  width: 160px;
  height: 160px;
  background: rgba(255, 130, 69, 0.12);
}

.hero-tag,
.hero-title,
.hero-desc,
.hero-status-row {
  position: relative;
  z-index: 1;
}

.hero-tag {
  display: inline-flex;
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  color: #9a5c1e;
  font-size: 13px;
}

.hero-title {
  margin-top: 22px;
  color: #2f2215;
  font-size: 36px;
  font-weight: 600;
  line-height: 1.2;
}

.hero-desc {
  margin-top: 14px;
  color: #7a6045;
  font-size: 15px;
  line-height: 1.65;
}

.hero-status-row,
.field-head,
.switch-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 18px;
}

.hero-status-row {
  margin-top: 24px;
}

.hero-status {
  display: inline-flex;
  align-items: center;
  padding: 10px 16px;
  border-radius: 999px;
  background: rgba(255, 138, 61, 0.12);
  color: #cf6a1b;
  font-size: 13px;
}

.hero-status-bound {
  background: rgba(48, 168, 88, 0.12);
  color: #2f9252;
}

.hero-status-dot {
  width: 10px;
  height: 10px;
  margin-right: 10px;
  border-radius: 50%;
  background: currentColor;
}

.hero-caption {
  color: #8d7257;
  font-size: 13px;
  line-height: 1.4;
}

.form-card {
  margin-top: 22px;
  padding: 30px 28px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 18px 46px rgba(82, 56, 28, 0.07);
}

.section-title {
  color: #2f2418;
  font-size: 22px;
  font-weight: 600;
}

.section-desc {
  margin-top: 10px;
  color: #86715c;
  font-size: 13px;
  line-height: 1.6;
}

.field-block {
  padding-top: 26px;
}

.field-label {
  color: #2f2418;
  font-size: 18px;
  font-weight: 600;
}

.field-helper {
  margin-top: 10px;
  color: #8e7c67;
  font-size: 13px;
  line-height: 1.5;
}

.login-status {
  flex-shrink: 0;
  display: inline-flex;
  padding: 10px 16px;
  border-radius: 999px;
  background: #f4eadb;
  color: #8e7c67;
  font-size: 13px;
}

.login-status-bound {
  background: #e8f6ee;
  color: #2d8a5a;
}

.text-input,
.text-area {
  width: 100%;
  margin-top: 18px;
  padding: 18px 20px;
  border: none;
  border-radius: 24px;
  background: linear-gradient(180deg, #fffaf4 0%, #faf4eb 100%);
  color: #2f2418;
  font-size: 15px;
}

.text-area {
  min-height: 160px;
  resize: vertical;
}

.code-row {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-top: 20px;
}

.code-input {
  flex: 1;
  margin-top: 0;
}

.send-code-button {
  flex-shrink: 0;
  border: none;
  padding: 0 22px;
  height: 58px;
  border-radius: 24px;
  background: #2f2418;
  color: #fff7ec;
  font-size: 14px;
  cursor: pointer;
}

.switch-panel {
  margin-top: 20px;
}

.switch-card {
  padding: 24px;
  margin-bottom: 16px;
  border-radius: 24px;
  background: linear-gradient(180deg, #fffaf3 0%, #fff 100%);
}

.switch-card:last-child {
  margin-bottom: 0;
}

.switch-copy {
  flex: 1;
}

.switch-input {
  width: 22px;
  height: 22px;
  accent-color: #ff8a3d;
}

.choice-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 22px;
}

.choice-chip {
  min-width: calc(50% - 8px);
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 16px;
  border: none;
  border-radius: 24px;
  background: linear-gradient(180deg, #fffaf4 0%, #faf4eb 100%);
  color: #4e3a24;
  font-size: 15px;
  cursor: pointer;
}

.choice-chip-active {
  background: linear-gradient(145deg, #fff0dd 0%, #ffe2c0 100%);
  color: #8a4c12;
  box-shadow: inset 0 0 0 2px rgba(255, 138, 61, 0.18);
}

.choice-check {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #eadcca;
}

.choice-check-active {
  background: linear-gradient(145deg, #ffb05e 0%, #ff8a3d 100%);
  box-shadow: 0 0 0 5px rgba(255, 138, 61, 0.14);
}

.highlight-tip {
  margin-top: 18px;
  padding: 16px 18px;
  border-radius: 22px;
  background: linear-gradient(145deg, rgba(255, 240, 221, 0.92) 0%, rgba(255, 249, 239, 0.98) 100%);
  color: #a05a1e;
  font-size: 13px;
  line-height: 1.6;
}

.submit-button {
  margin-top: 30px;
  width: 100%;
  border: none;
  border-radius: 999px;
  background: linear-gradient(135deg, #ffb05e 0%, #ff8a3d 100%);
  color: #fff;
  font-size: 16px;
  line-height: 56px;
  box-shadow: 0 16px 34px rgba(255, 138, 61, 0.24);
  cursor: pointer;
}
</style>
