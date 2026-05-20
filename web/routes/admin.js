const router           = require('express').Router();
const bcrypt           = require('bcryptjs');
const { requireAdmin } = require('../middleware');
const db               = require('../db');

router.get('/', requireAdmin, (req, res) => {
  const users = db.prepare(`
    SELECT id, username, role, status, created_at,
           kalshi_email,
           CASE WHEN kalshi_api_key_enc IS NOT NULL THEN 1
                WHEN kalshi_password_enc IS NOT NULL THEN 1
                ELSE 0 END AS has_kalshi
    FROM users ORDER BY created_at DESC
  `).all();

  const stats = {
    total:     users.length,
    pending:   users.filter(u => u.status === 'pending').length,
    approved:  users.filter(u => u.status === 'approved').length,
    connected: users.filter(u => u.has_kalshi).length,
  };

  res.render('admin', {
    user: req.session.user,
    users,
    stats,
    flash: req.query.flash || null,
    error: req.query.error || null,
  });
});

router.post('/approve/:id', requireAdmin, (req, res) => {
  db.prepare("UPDATE users SET status = 'approved' WHERE id = ?").run(req.params.id);
  res.redirect('/admin');
});

router.post('/deny/:id', requireAdmin, (req, res) => {
  db.prepare("UPDATE users SET status = 'denied' WHERE id = ?").run(req.params.id);
  res.redirect('/admin');
});

router.post('/promote/:id', requireAdmin, (req, res) => {
  db.prepare("UPDATE users SET role = 'admin', status = 'approved' WHERE id = ?").run(req.params.id);
  res.redirect('/admin');
});

router.post('/demote/:id', requireAdmin, (req, res) => {
  if (parseInt(req.params.id) === req.session.user.id) return res.redirect('/admin');
  db.prepare("UPDATE users SET role = 'user' WHERE id = ?").run(req.params.id);
  res.redirect('/admin');
});

router.post('/delete/:id', requireAdmin, (req, res) => {
  if (parseInt(req.params.id) === req.session.user.id) return res.redirect('/admin');
  db.prepare('DELETE FROM users WHERE id = ?').run(req.params.id);
  res.redirect('/admin');
});

router.post('/create', requireAdmin, (req, res) => {
  const { username, password, role } = req.body;
  if (!username || !password || username.trim().length < 3 || password.length < 6) {
    return res.redirect('/admin?error=Username+must+be+3%2B+chars+and+password+6%2B+chars');
  }
  const existing = db.prepare('SELECT id FROM users WHERE username = ?').get(username.trim());
  if (existing) return res.redirect('/admin?error=Username+already+taken');

  const hash = bcrypt.hashSync(password, 10);
  db.prepare('INSERT INTO users (username, password, role, status) VALUES (?, ?, ?, ?)')
    .run(username.trim(), hash, role === 'admin' ? 'admin' : 'user', 'approved');
  res.redirect('/admin?flash=User+created');
});

router.post('/reset-password/:id', requireAdmin, (req, res) => {
  const { new_password } = req.body;
  if (!new_password || new_password.length < 6) {
    return res.redirect('/admin?error=Password+must+be+6%2B+characters');
  }
  const hash = bcrypt.hashSync(new_password, 10);
  db.prepare('UPDATE users SET password = ? WHERE id = ?').run(hash, req.params.id);
  res.redirect('/admin?flash=Password+reset');
});

module.exports = router;
