const INNER_PHONE_REG = '^1(?:3\\d|4[4-9]|5[0-35-9]|6[67]|7[0-8]|8\\d|9\\d)\\d{8}$';

export function phoneEncryption(phone: string) {
  return phone.replace(/(\d{3})\d{4}(\d{4})/, '$1****$2');
}

export function phoneRegCheck(phone: string) {
  return new RegExp(INNER_PHONE_REG).test(phone);
}
