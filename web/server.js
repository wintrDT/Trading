require('dotenv').config({ path: require('path').join(__dirname, '../.env') });

const express = require('express');
const session = require('express-session');
const path    = require('path');

const app = express();

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));
app.use(express.static(path.join(__dirname, 'public')));
app.use(express.urlencoded({ extended: true }));
app.use(express.json());

app.use(session({
  secret:            process.env.SESSION_SECRET || 'kalshi-bot-secret-change-me',
  resave:            false,
  saveUninitialized: false,
  cookie:            { maxAge: 7 * 24 * 60 * 60 * 1000 }, // 7 days
}));

app.use('/auth',      require('./routes/auth'));
app.use('/admin',     require('./routes/admin'));
app.use('/markets',   require('./routes/markets'));
app.use('/spreads',   require('./routes/spreads'));
app.use('/portfolio', require('./routes/portfolio'));
app.use('/settings',  require('./routes/settings'));
app.use('/options',   require('./routes/options'));
app.use('/futures',   require('./routes/futures'));
app.use('/crypto',    require('./routes/crypto'));
app.use('/',          require('./routes/dashboard'));

// Error handler — return JSON for API routes, HTML for pages
app.use((err, req, res, next) => {
  console.error('[web]', err.message, err.stack);
  if (req.path.startsWith('/generate') || req.path.startsWith('/auth/') || req.headers['content-type']?.includes('application/json')) {
    return res.status(500).json({ error: err.message || 'Internal server error' });
  }
  res.status(500).render('error', { message: 'Something went wrong.' });
});

const PORT = process.env.PORT || process.env.WEB_PORT || 3000;
app.listen(PORT, () => console.log(`🌐 Dashboard running at http://localhost:${PORT}`));

module.exports = app;
