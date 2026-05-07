import { getDietProfile } from './dietProfile';
import { getMeicanSession } from './meicanSession';

function isNumericMeicanUserKey(value: string) {
  return /^\d{8,19}$/.test(`${value || ''}`.trim());
}

export function stableRecommendUserId() {
  const session = getMeicanSession() || {};
  const profile = getDietProfile() || {};
  const candidates = [session.snowflakeId, profile.meicanMemberId].map((item) => `${item || ''}`.trim());

  for (const candidate of candidates) {
    if (isNumericMeicanUserKey(candidate)) {
      return candidate;
    }
  }

  return '';
}
