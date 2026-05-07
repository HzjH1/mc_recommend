import { ref } from 'vue';

export const toastMessage = ref('');

let toastTimer: number | undefined;

export function showToast(message: string, duration = 2200) {
  toastMessage.value = message;
  if (toastTimer) {
    window.clearTimeout(toastTimer);
  }
  toastTimer = window.setTimeout(() => {
    toastMessage.value = '';
  }, duration);
}
