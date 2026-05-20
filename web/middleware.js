function requireAuth(req, res, next) {
  if (!req.session.user) return res.redirect('/auth/login');
  next();
}

function requireAdmin(req, res, next) {
  if (!req.session.user) return res.redirect('/auth/login');
  if (req.session.user.role !== 'admin') return res.status(403).render('error', { message: 'Admin access only.' });
  next();
}

module.exports = { requireAuth, requireAdmin };
