const router = require('express').Router();
const bcrypt = require('bcryptjs');
const db     = require('../db');

router.get('/login', (req, res) => {
  if (req.session.user) return res.redirect('/');
  res.render('login', { error: null, message: req.query.registered ? 'Account created — waiting for admin approval.' : null });
});

router.post('/login', (req, res) => {
  const { username, password } = req.body;
  const user = db.prepare('SELECT * FROM users WHERE username = ?').get(username?.trim());
  if (!user || !bcrypt.compareSync(password, user.password)) {
    return res.render('login', { error: 'Invalid username or password.', message: null });
  }
  if (user.status === 'pending') {
    return res.render('login', { error: null, message: 'Your account is pending admin approval.' });
  }
  if (user.status === 'denied') {
    return res.render('login', { error: 'Your access has been denied.', message: null });
  }
  req.session.user = { id: user.id, username: user.username, role: user.role };
  res.redirect('/');
});

router.get('/register', (req, res) => {
  if (req.session.user) return res.redirect('/');
  res.render('register', { error: null });
});

router.post('/register', (req, res) => {
  const { username, password } = req.body;
  if (!username || !password || username.trim().length < 3 || password.length < 6) {
    return res.render('register', { error: 'Username 3+ chars · Password 6+ chars.' });
  }
  const existing = db.prepare('SELECT id FROM users WHERE username = ?').get(username.trim());
  if (existing) return res.render('register', { error: 'Username already taken.' });

  const hash  = bcrypt.hashSync(password, 10);
  const count = db.prepare('SELECT COUNT(*) as c FROM users').get().c;
  // First registered user becomes admin (auto-approved)
  const role   = count === 0 ? 'admin' : 'user';
  const status = count === 0 ? 'approved' : 'pending';

  db.prepare('INSERT INTO users (username, password, role, status) VALUES (?, ?, ?, ?)')
    .run(username.trim(), hash, role, status);

  if (role === 'admin') {
    const user = db.prepare('SELECT * FROM users WHERE username = ?').get(username.trim());
    req.session.user = { id: user.id, username: user.username, role: user.role };
    return res.redirect('/');
  }
  res.redirect('/auth/login?registered=1');
});

router.get('/logout', (req, res) => {
  req.session.destroy(() => res.redirect('/auth/login'));
});

module.exports = router;
