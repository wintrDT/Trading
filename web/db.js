const Database = require('better-sqlite3');
const path     = require('path');
const fs       = require('fs');

const dataDir = path.join(__dirname, '../data');
if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

const db = new Database(path.join(dataDir, 'users.db'));

db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT UNIQUE NOT NULL,
    password            TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'user',
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    kalshi_email        TEXT,
    kalshi_password_enc TEXT
  )
`);

// Migrate existing databases — add columns if they don't exist yet
const cols = db.prepare("PRAGMA table_info(users)").all().map(c => c.name);
if (!cols.includes('kalshi_email'))        db.exec("ALTER TABLE users ADD COLUMN kalshi_email TEXT");
if (!cols.includes('kalshi_password_enc')) db.exec("ALTER TABLE users ADD COLUMN kalshi_password_enc TEXT");
if (!cols.includes('kalshi_api_key_enc'))  db.exec("ALTER TABLE users ADD COLUMN kalshi_api_key_enc TEXT");

module.exports = db;
