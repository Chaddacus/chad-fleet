/* Generate a HUB_AUTH_PASSWORD_HASH value. Usage: node features/auth/hash-password.mjs <password> */
import { randomBytes, scryptSync } from 'node:crypto';

const password = process.argv[2];
if (!password) {
  console.error('usage: node features/auth/hash-password.mjs <password>');
  process.exit(1);
}
const salt = randomBytes(16);
const derived = scryptSync(password, salt, 64);
console.log(`scrypt$${salt.toString('hex')}$${derived.toString('hex')}`);
