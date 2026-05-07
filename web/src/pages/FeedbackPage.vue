<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { profileState } from '../shared/dietProfile';
import { getTranslations } from '../shared/i18n';
import { showToast } from '../shared/toast';

const router = useRouter();
const content = ref('');
const contact = ref('');

const texts = computed(() => getTranslations(profileState.value.language).feedback);
const commonTexts = computed(() => getTranslations(profileState.value.language).common);

function submitFeedback() {
  if (!`${content.value || ''}`.trim()) {
    showToast(texts.value.inputLabel);
    return;
  }

  showToast(commonTexts.value.feedbackSubmitted);
  window.setTimeout(() => {
    if (window.history.length > 1) {
      router.back();
      return;
    }
    router.replace('/mine');
  }, 260);
}

onMounted(() => {
  contact.value = profileState.value.phone || profileState.value.email || '';
});
</script>

<template>
  <div class="page-shell feedback-page">
    <div class="intro-card">
      <div class="intro-title">{{ texts.title }}</div>
      <div class="intro-desc">{{ texts.desc }}</div>
    </div>

    <div class="form-card">
      <div class="field-block">
        <div class="field-label">{{ texts.inputLabel }}</div>
        <textarea v-model="content" class="text-area" maxlength="300" :placeholder="texts.inputPlaceholder"></textarea>
      </div>

      <div class="field-block">
        <div class="field-label">{{ texts.contactLabel }}</div>
        <input v-model="contact" class="text-input" :placeholder="texts.contactPlaceholder" />
      </div>
    </div>

    <button class="submit-button" @click="submitFeedback">{{ texts.submit }}</button>
  </div>
</template>

<style scoped>
.feedback-page {
  background:
    radial-gradient(circle at top left, rgba(255, 223, 173, 0.35), transparent 34%),
    linear-gradient(180deg, #fff8ef 0%, #f6efe5 100%);
  padding-bottom: calc(env(safe-area-inset-bottom) + 48px);
}

.intro-card,
.form-card {
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 16px 40px rgba(82, 56, 28, 0.06);
}

.intro-card {
  padding: 30px;
}

.intro-title {
  color: #2f2418;
  font-size: 30px;
  font-weight: 600;
}

.intro-desc {
  margin-top: 14px;
  color: #756451;
  font-size: 15px;
  line-height: 1.7;
}

.form-card {
  margin-top: 22px;
  padding: 8px 28px;
}

.field-block {
  padding: 28px 0;
  border-bottom: 1px solid #f0e5d9;
}

.field-block:last-child {
  border-bottom: none;
}

.field-label {
  color: #2f2418;
  font-size: 18px;
  font-weight: 600;
}

.text-input,
.text-area {
  width: 100%;
  margin-top: 18px;
  padding: 18px 20px;
  border: none;
  border-radius: 22px;
  background: #faf5ee;
  color: #2f2418;
  font-size: 15px;
}

.text-area {
  min-height: 180px;
  resize: vertical;
}

.submit-button {
  margin-top: 28px;
  width: 100%;
  border: none;
  border-radius: 999px;
  background: #ff8a3d;
  color: #fff;
  font-size: 16px;
  line-height: 54px;
  cursor: pointer;
}
</style>
