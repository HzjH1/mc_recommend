import { saveDietProfile, type DietProfile } from './dietProfile';
import { saveMeicanSession } from './meicanSession';
import { syncRecommendBackendSession } from './recommendSessionSync';

function mockNumericSnowflakeForPhone(phone: string) {
  const digits = `${phone || ''}`.replace(/\D/g, '') || '13000000000';
  const core = `${digits}00000000000000`.slice(0, 15);
  return `2${core}`;
}

function buildMockSessionPayload(phone: string, verificationCode: string) {
  return {
    phone,
    accessToken: `mock-access-${phone}`,
    refreshToken: `mock-refresh-${phone}`,
    ticket: `ticket-${verificationCode || '0000'}`,
    snowflakeId: mockNumericSnowflakeForPhone(phone),
    signature: `signature-${phone.slice(-4)}`,
    selectedAccountName: '默认企业账户',
    accountNamespace: 'mock-corp',
    accountNamespaceLunch: 'mock-corp',
    accountNamespaceDinner: 'mock-corp',
    accessTokenExpiresIn: 3600,
  };
}

function buildMockProfile(phone: string): Partial<DietProfile> {
  const tail = `${phone}`.slice(-4);
  return {
    phone,
    meicanName: `张${tail}`,
    meicanMemberId: mockNumericSnowflakeForPhone(phone),
    meicanExternalMemberId: phone,
    meicanEmployeeNo: `EMP${tail}`,
    email: `meal${tail}@meican.local`,
    avatarText: '张',
    corpNames: ['示例企业'],
    meicanCorpNamespace: 'mock-corp',
    userType: 'CORP_MEMBER',
    balance: '42.50',
    accountStatus: 'ACTIVE',
  };
}

export async function sendPhoneVerificationCode(phone: string) {
  return {
    success: true,
    sent: true,
    phone,
  };
}

export async function syncMeicanProfileByPhone(params: { phone: string; verificationCode: string }) {
  saveMeicanSession(buildMockSessionPayload(params.phone, params.verificationCode));
  const profile = saveDietProfile({
    ...(buildMockProfile(params.phone) as Partial<DietProfile>),
    ...buildMockProfile(params.phone),
  });
  await syncRecommendBackendSession(params.phone);
  return profile;
}
