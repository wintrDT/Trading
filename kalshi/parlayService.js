// kalshi/parlayService.js
// Pure business logic — no Discord.js dependency.
// Used by both the Discord command and the web dashboard.

const { getSportsMarkets, getMarkets, publicRequest } = require('./kalshiClient');
const { buildKalshiParlay, getYesPrice }              = require('./kalshiAnalyzer');
const { fetchPlayerProps }                             = require('../utils/propsApi');
const { savePick }                                     = require('../utils/pickTracker');
const { fetchSportsContext }                           = require('../utils/sportsData');

// ── Config ────────────────────────────────────────────────────────────────────
const TIER_CONFIG = {
  safe:   { label: 'SAFE PICKS',        legs: 3 },
  medium: { label: 'MEDIUM RISK PICKS', legs: 4 },
  high:   { label: 'HIGH RISK PICKS',   legs: 5 },
};

// ── Date filter ───────────────────────────────────────────────────────────────
function filterToday(markets) {
  const now = Date.now();
  // End of today's calendar day + 6h buffer to catch late-night games
  const endOfDay = new Date();
  endOfDay.setHours(23, 59, 59, 999);
  const cutoff = endOfDay.getTime() + 6 * 60 * 60 * 1000;
  const result = markets.filter(m => {
    const tf = m.close_time ?? m.expiration_time ?? m.end_date ?? null;
    if (!tf) return true;
    const t = new Date(tf).getTime();
    return t >= now && t <= cutoff;
  });
  console.log(`[filterToday] ${markets.length} → ${result.length}`);
  return result.length >= 3 ? result : markets;
}

// ── Keyword filter map ────────────────────────────────────────────────────────
const SPORT_KEYWORD_MAP = {
  nba: ['lebron','curry','durant','giannis','jokic','embiid','tatum','brown','maxey',
        'edwards','booker','towns','holmgren','sengun','thompson','mccollum','randle',
        'george','hartenstein','avdija','white','pritchard','brooks','green','fox',
        'wembanyama','lillard','morant','wagner','mobley','garland','markkanen',
        'gilgeous','sga','dort','brunson','anunoby','siakam','ingram','lavine',
        'bane','smart','dinwiddie','poole','kuzma','kxnba'],
  mlb: ['judge','ohtani','soto','betts','ramirez','buxton','guerrero','alvarez',
        'raleigh','henderson','delacruz','altuve','schwarber','seager','trout',
        'yankees','dodgers','mets','cubs','astros','braves','red sox','padres',
        'giants','cardinals','phillies','blue jays','mariners','guardians','kxmlb'],
  nfl: ['mahomes','burrow','jackson','allen','hurts','stroud','love','herbert',
        'chiefs','eagles','bills','cowboys','packers','ravens','niners','49ers',
        'bengals','lions','bears','giants','jets','patriots','dolphins','steelers',
        'chargers','broncos','raiders','seahawks','rams','saints','falcons',
        'panthers','buccaneers','texans','colts','titans','jaguars','vikings',
        'commanders','cardinals','browns','kxnfl'],
  nhl: ['ovechkin','mcdavid','draisaitl','mackinnon','matthews','hedman',
        'rangers','bruins','leafs','lightning','panthers','hurricanes','oilers',
        'avalanche','golden knights','caps','capitals','kxnhl'],
  thunder:      ['gilgeous','sga','holmgren','dort','wallace','williams','okc'],
  celtics:      ['tatum','brown','white','pritchard','holiday','boston'],
  knicks:       ['brunson','towns','hartenstein','anunoby','randle','new york'],
  warriors:     ['curry','thompson','green','poole','wiggins','golden state'],
  lakers:       ['lebron','davis','reaves','hachimura','los angeles lakers'],
  nuggets:      ['jokic','murray','gordon','porter','denver'],
  heat:         ['butler','adebayo','herro','lowry','miami'],
  pacers:       ['haliburton','siakam','nembhard','turner','indiana'],
  cavaliers:    ['mobley','garland','allen','mitchell','cleveland'],
  bucks:        ['giannis','lillard','middleton','portis','milwaukee'],
  rockets:      ['green','sengun','amen','thompson','edgecombe','houston'],
  grizzlies:    ['morant','bane','jackson','smart','memphis'],
  clippers:     ['george','leonard','harden','zubac','los angeles clippers'],
  suns:         ['booker','durant','beal','nurkic','phoenix'],
  mavericks:    ['doncic','irving','lively','dinwiddie','dallas'],
  mavs:         ['doncic','irving','lively','dinwiddie','dallas'],
  pelicans:     ['zion','ingram','mccollum','valanciunas','new orleans'],
  kings:        ['fox','sabonis','monk','huerter','sacramento'],
  hawks:        ['young','murray','hunter','okongwu','atlanta'],
  bulls:        ['lavine','vucevic','ball','caruso','chicago'],
  hornets:      ['ball','bridges','hayward','rozier','charlotte'],
  pistons:      ['cunningham','ivey','duren','bogdanovic','detroit'],
  magic:        ['banchero','wagner','fultz','harris','orlando'],
  wizards:      ['poole','kuzma','porzingis','beal','washington'],
  blazers:      ['lillard','simons','grant','little','portland'],
  jazz:         ['markkanen','clarkson','collins','sexton','utah'],
  spurs:        ['wembanyama','johnson','vassell','sochan','san antonio'],
  timberwolves: ['edwards','towns','gobert','conley','minnesota','wolves'],
  raptors:      ['barnes','siakam','anunoby','quickley','toronto'],
};

// ── Ticker → stat label ───────────────────────────────────────────────────────
const TICKER_STAT = {
  KXNBAPTS:   'points',    KXNBAAST:  'assists',  KXNBAREB:  'rebounds',
  KXNBATHRPM: '3-pointers',KXNBASTL:  'steals',   KXNBABLK:  'blocks',
  KXMLBHITS:  'hits',      KXMLBHR:   'home runs', KXMLBRBI: 'RBIs',
  KXMLBSO:    'strikeouts',
  KXNFLPASS:  'pass yds',  KXNFLRUSH: 'rush yds', KXNFLREC: 'rec yds',
  KXNFLTD:    'TDs',
};

function statFromTicker(ticker) {
  const t = (ticker || '').toUpperCase();
  for (const [prefix, stat] of Object.entries(TICKER_STAT)) {
    if (t.startsWith(prefix)) return stat;
  }
  return null;
}

// ── Name / title helpers ──────────────────────────────────────────────────────
function expandName(str) {
  // Keys must be abbreviated forms only — never a word that appears inside a full name
  // (e.g. avoid 'Lebron' which re-matches inside 'LeBron James').
  const MAP = {
    'Jbrown':'Jaylen Brown','Jtatum':'Jayson Tatum','Jholiday':'Jrue Holiday',
    'Tmaxey':'Tyrese Maxey','Pgeorge':'Paul George','Kdurant':'Kevin Durant',
    'Ljames':'LeBron James','Adavis':'Anthony Davis',
    'Jmurray':'Jamal Murray','Njokic':'Nikola Jokic','Lball':'LaMelo Ball',
    'Aedwards':'Anthony Edwards','Sedwards':'Anthony Edwards',
    'Dfox':"De'Aaron Fox",'Dclingan':'Donovan Clingan',
    'Vedgecombe':'VJ Edgecombe','Asengun':'Alperen Sengun',
    'Davdija':'Deni Avdija','Dpritchard':'Payton Pritchard',
    'Dwhite':'Derrick White','Ppritchard':'Payton Pritchard',
    'Sgilgeousalexander':'Shai Gilgeous-Alexander','Sga':'Shai Gilgeous-Alexander',
    'Ldort':'Luguentz Dort','Kleonard':'Kawhi Leonard',
    'Jsmith':'Jabari Smith Jr.','Koubre':'Kelly Oubre Jr.',
    'Nqueta':'Neemias Queta','Nvucevic':'Nikola Vucevic',
    'Sbarnes':'Scottie Barnes','Oaninoby':'OG Anunoby','Oanunoby':'OG Anunoby',
    'Bsiakam':'Pascal Siakam','Bingram':'Brandon Ingram','Zlavine':'Zach LaVine',
    'Dlillard':'Damian Lillard','Jmorant':'Ja Morant','Dbook':'Devin Booker',
    'Cpaul':'Chris Paul','Pwagner':'Franz Wagner','Fwagner':'Franz Wagner',
    'Jhartenstein':'Isaiah Hartenstein','Ihartenstein':'Isaiah Hartenstein',
    'Edelacruz':'Elly De La Cruz','Jaltuve':'Jose Altuve',
    'Ajudge':'Aaron Judge','Jsoto':'Juan Soto',
    'Kschwarber':'Kyle Schwarber','Vguerrero':'Vladimir Guerrero Jr.',
    'Tstory':'Trevor Story','Yalvarez':'Yordan Alvarez',
    'Craleigh':'Cal Raleigh','Ghenderson':'Gunnar Henderson',
    'Cmccollum':'CJ McCollum','Cjmccollum':'CJ McCollum',
    'Jrandle':'Julius Randle','Dbooker':'Devin Booker','Msmart':'Marcus Smart',
    'Athompson':'Amen Thompson','Athom':'Amen Thompson',
    'Vwembanyama':'Victor Wembanyama','Wemby':'Victor Wembanyama',
    'Gantetokounmpo':'Giannis Antetokounmpo','Aantetokounmpo':'Giannis Antetokounmpo',
    'Emobley':'Evan Mobley','Hmobley':'Evan Mobley',
    'Dgarland':'Darius Garland','Jallen':'Jarrett Allen',
    'Lmarkkanen':'Lauri Markkanen','Scurry':'Stephen Curry',
    'Kthompson':'Klay Thompson','Tcurry':'Stephen Curry',
    'Jramrez':'Jose Ramirez','Jramirez':'Jose Ramirez','Bbuxton':'Byron Buxton',
    'Sohtani':'Shohei Ohtani','Mbetts':'Mookie Betts','Jbetts':'Mookie Betts',
    'Jembiid':'Joel Embiid',
  };
  let r = str;
  for (const [k, v] of Object.entries(MAP)) {
    r = r.replace(new RegExp('\\b' + k + '\\b', 'gi'), v);
  }
  return r;
}

function isCleanTitle(title) {
  if (!title || title.length < 8) return false;
  const t = title.trim();
  if (/^[\d,\s]+$/.test(t)) return false;
  if (/^(no|yes):/i.test(t)) return false;
  if (/^[A-Z]{2,4}\s+win/i.test(t)) return false;
  if (/^[A-Z]{2,4},\s*[A-Z0-9]{1,4}$/.test(t)) return false;
  const stripped = t.replace(/^\d+,\s*/, '').trim();
  if (stripped !== t) return isCleanTitle(stripped);
  return /\d+\+/.test(t) || /winner|advance|series/i.test(t);
}

function cleanTitle(title) {
  if (!title) return 'Unknown Market';
  return title.split(',').map(s => {
    let p = s.trim()
      .replace(/^\d+\s*$/, '').replace(/^\d+\s+/, '').trim()
      .replace(/^yes /i, '').replace(/^no /i, '').trim();
    return expandName(p);
  })
  .filter(p => {
    if (!p || p.length < 3) return false;
    if (/^\d+$/.test(p)) return false;
    if (/^(NO:|YES:)/i.test(p)) return false;
    if (/^[A-Z]{2,4}\s+Win$/i.test(p)) return false;
    if (/^Total\s+\d+$/i.test(p)) return false;
    return true;
  })
  .filter((v, i, a) => a.indexOf(v) === i)
  .join(', ');
}

function decodeMarketTitle(market) {
  let title;
  if (market._enrichedTitle) {
    title = cleanTitle(market._enrichedTitle);
  } else {
    const raw = market.no_sub_title || market.title || '';
    title = raw ? cleanTitle(raw) : (market.ticker || 'Unknown Market');
  }

  // Individual KXNBA/KXMLB/KXNFL markets often omit the stat type from the title.
  // Detect "Name: N+" with no following word and append it from the ticker prefix.
  if (/\d+\+\s*$/.test(title)) {
    const stat = statFromTicker(market.ticker) || statFromTicker(market.series_ticker || '');
    if (stat) title = title.trimEnd() + ' ' + stat;
  }

  return title;
}

async function enrichLegTitles(legs) {
  const enriched = await Promise.all(legs.map(async m => {
    try {
      const subLegs = m.mve_selected_legs || [];
      if (!subLegs.length) return m;
      const titles = (await Promise.all(
        subLegs.slice(0, 8).map(async leg => {
          try {
            const data       = await publicRequest('GET', `/markets/${leg.market_ticker}`, {}, 60 * 60);
            const marketData = data?.market ?? data;
            const raw        = marketData?.title;
            if (!raw) return null;
            let expanded = expandName(raw.replace(/^[\d,\s]+/, '').trim() || raw);
            if (!isCleanTitle(expanded)) return null;
            // Append stat type when the sub-leg title ends with N+ but has no stat word
            if (/\d+\+\s*$/.test(expanded)) {
              const stat = statFromTicker(leg.market_ticker) || statFromTicker(marketData?.series_ticker || '');
              if (stat) expanded = expanded.trimEnd() + ' ' + stat;
            }
            return expanded;
          } catch { return null; }
        })
      )).filter(Boolean);
      if (!titles.length) return m;
      return { ...m, _enrichedTitle: titles.join(', ') };
    } catch { return m; }
  }));
  return enriched;
}

// ── Main entry point ──────────────────────────────────────────────────────────
/**
 * Generates parlays for all (or selected) tiers.
 * Returns structured plain-JS data — no Discord objects.
 * Also saves each built parlay to picks.json as a side effect.
 *
 * @param {object} opts
 * @param {string} opts.tier      'all' | 'safe' | 'medium' | 'high'
 * @param {string} opts.filter    keyword filter (e.g. 'nba', 'celtics')
 * @param {boolean} opts.useAll   include all Kalshi markets, not just sports
 * @param {boolean} opts.save     whether to save to picks.json (default true)
 */
async function generateParlays({ tier = 'all', filter = null, useAll = false, save = true } = {}) {
  let rawMarkets = [];
  let propMap = {};

  // Fetch markets and props separately so one failure doesn't block everything
  try {
    rawMarkets = await (useAll
      ? getMarkets({ status: 'open', limit: 200 }).then(d => d?.markets || [])
      : getSportsMarkets());
    console.log(`[generateParlays] Fetched ${rawMarkets.length} markets`);
  } catch (err) {
    console.error('[generateParlays] Failed to fetch markets:', err.message);
    throw new Error('Failed to fetch Kalshi markets: ' + err.message);
  }

  try {
    propMap = await fetchPlayerProps();
  } catch (err) {
    console.warn('[generateParlays] Failed to fetch props, continuing without Vegas edge:', err.message);
    propMap = {};
  }

  let markets = filterToday(rawMarkets);

  let sportsContext = null;
  try {
    sportsContext = await fetchSportsContext(markets, expandName);
  } catch (err) {
    console.warn('[generateParlays] Sports context failed, continuing without real-world data:', err.message);
  }

  if (filter) {
    const keywords = SPORT_KEYWORD_MAP[filter.toLowerCase()] ?? [filter.toLowerCase()];
    markets = markets.filter(m => {
      const text = [m.title, m.ticker, m.subtitle, m.no_sub_title, m.category]
        .filter(Boolean).join(' ').toLowerCase();
      return keywords.some(k => text.includes(k));
    });
  }

  const date       = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
  const withPrices = markets.filter(m => getYesPrice(m) != null);
  const tiers      = tier === 'all' ? ['safe', 'medium', 'high'] : [tier];
  const results    = [];

  for (const t of tiers) {
    const cfg    = TIER_CONFIG[t];
    const parlay = buildKalshiParlay(withPrices, t, cfg.legs, propMap, sportsContext);
    if (!parlay) continue;

    parlay.legs = await enrichLegTitles(parlay.legs);

    // Attach decoded titles so callers can display them
    parlay.legs = parlay.legs.map(m => ({
      ...m,
      _decodedTitle: decodeMarketTitle(m),
    }));

    const savedId = save ? savePick(parlay, cfg.label, date) : null;

    results.push({
      id:           savedId,
      tier:         t,
      label:        cfg.label,
      date,
      legs:         parlay.legs,
      combinedProb: (parlay.combinedProb * 100).toFixed(1),
      totalCost:    parlay.totalCost,
      totalPayout:  parlay.totalPayout,
      profit:       parlay.profit,
      roi:          parlay.roi,
    });
  }

  return {
    results,
    date,
    marketCount: markets.length,
    priceCount:  withPrices.length,
    filter,
  };
}

module.exports = {
  generateParlays,
  enrichLegTitles,
  decodeMarketTitle,
  filterToday,
  SPORT_KEYWORD_MAP,
  TIER_CONFIG,
};
