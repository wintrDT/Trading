// utils/cache.js — Simple TTL in-memory cache

const store = new Map();

function set(key, value, ttlSeconds) {
  store.set(key, {
    value,
    expiresAt: Date.now() + ttlSeconds * 1000,
  });
}

function get(key) {
  const entry = store.get(key);
  if (!entry) return null;
  if (Date.now() > entry.expiresAt) {
    store.delete(key);
    return null;
  }
  return entry.value;
}

function has(key) {
  const entry = store.get(key);
  return !!entry && Date.now() <= entry.expiresAt;
}

function del(key) {
  store.delete(key);
}

function clear() {
  store.clear();
}

module.exports = { set, get, has, del, clear };
