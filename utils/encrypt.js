// utils/encrypt.js — AES-256-CBC encrypt/decrypt for Kalshi passwords at rest
const crypto = require('crypto');

// Derive a 32-byte key from ENCRYPTION_KEY env var, or fall back to SESSION_SECRET.
// Either way we need something consistent across restarts.
function getKey() {
  const raw = process.env.ENCRYPTION_KEY || process.env.SESSION_SECRET || 'kalshi-bot-fallback-key-change-me';
  return crypto.createHash('sha256').update(raw).digest(); // always 32 bytes
}

function encrypt(plaintext) {
  const key = getKey();
  const iv  = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv('aes-256-cbc', key, iv);
  const enc = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  return iv.toString('hex') + ':' + enc.toString('hex');
}

function decrypt(stored) {
  if (!stored || !stored.includes(':')) return null;
  try {
    const [ivHex, encHex] = stored.split(':');
    const key     = getKey();
    const iv      = Buffer.from(ivHex, 'hex');
    const enc     = Buffer.from(encHex, 'hex');
    const decipher = crypto.createDecipheriv('aes-256-cbc', key, iv);
    return Buffer.concat([decipher.update(enc), decipher.final()]).toString('utf8');
  } catch { return null; }
}

module.exports = { encrypt, decrypt };
