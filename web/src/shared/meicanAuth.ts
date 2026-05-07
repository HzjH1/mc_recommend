import { loginMeicanByPhone, sendMeicanPhoneVerificationCode } from '../api';
import { saveDietProfile, type DietProfile } from './dietProfile';
import { saveMeicanSession } from './meicanSession';

export async function sendPhoneVerificationCode(phone: string) {
  return sendMeicanPhoneVerificationCode(phone);
}

export async function syncMeicanProfileByPhone(params: { phone: string; verificationCode: string }) {
  const result = await loginMeicanByPhone(params);
  saveMeicanSession(result.session);
  return saveDietProfile({
    ...(result.profile as Partial<DietProfile>),
    phone: result.profile.phone || params.phone,
  });
}
