import { putMeicanSession } from '../api';
import { getDietProfile } from './dietProfile';
import { getMeicanSession } from './meicanSession';
import { stableRecommendUserId } from './recommendUserId';

export async function syncRecommendBackendSession(phone?: string) {
  const session = getMeicanSession();
  if (!session?.accessToken) {
    return;
  }

  const userId = stableRecommendUserId();
  if (!userId) {
    return;
  }

  const profile = getDietProfile() || {};
  const sessionNamespace = `${session.accountNamespace || ''}`.trim();
  const lunchNamespace = `${session.accountNamespaceLunch || ''}`.trim();
  const dinnerNamespace = `${session.accountNamespaceDinner || ''}`.trim();
  const profileNamespace = `${profile.meicanCorpNamespace || ''}`.trim();
  const accountNamespace = profileNamespace || sessionNamespace;

  if (!accountNamespace) {
    return;
  }

  try {
    await putMeicanSession(userId, {
      accessToken: session.accessToken,
      refreshToken: session.refreshToken || '',
      expiresIn: session.accessTokenExpiresIn || 3600,
      phone: phone || session.phone,
      meicanUsername: session.selectedAccountName,
      accountNamespace,
      accountNamespaceLunch: lunchNamespace,
      accountNamespaceDinner: dinnerNamespace,
    });
  } catch {
    // 不阻断页面逻辑，后续刷新可再次同步
  }
}
