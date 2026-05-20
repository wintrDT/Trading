// utils/statsFormatter.js
// Parses raw ESPN stats API response into clean, readable embed fields

// ESPN returns stats as arrays with a separate "names" array — zip them together
function zipStats(statsObj) {
  if (!statsObj) return {};
  const { names = [], displayValues = [], values = [] } = statsObj;
  const result = {};
  names.forEach((name, i) => {
    result[name] = {
      display: displayValues[i] ?? '--',
      raw: values[i] ?? 0,
    };
  });
  return result;
}

// Pull a stat display value safely
function stat(zipped, key, fallback = '--') {
  return zipped[key]?.display ?? fallback;
}

// ── NBA Formatter ─────────────────────────────────────────────────────────────
function formatNBAStats(statsData, playerName) {
  const categories = statsData?.splits?.categories || [];

  const general = categories.find(c => c.name === 'general' || c.displayName === 'General');
  const perGame = categories.find(c => c.name === 'perGame' || c.displayName === 'Per Game');
  const advanced = categories.find(c => c.name === 'advanced' || c.displayName === 'Advanced');

  const g = zipStats(general?.stats?.[0] || general?.totals);
  const pg = zipStats(perGame?.stats?.[0] || perGame?.totals);
  const adv = zipStats(advanced?.stats?.[0] || advanced?.totals);

  const fields = [];

  if (Object.keys(pg).length || Object.keys(g).length) {
    const src = Object.keys(pg).length ? pg : g;
    fields.push({
      name: '📊 Per Game Averages',
      value: [
        `🏀 PPG: \`${stat(src, 'avgPoints', stat(src, 'points'))}\``,
        `🔄 RPG: \`${stat(src, 'avgRebounds', stat(src, 'rebounds'))}\``,
        `🎯 APG: \`${stat(src, 'avgAssists', stat(src, 'assists'))}\``,
        `🛡️ SPG: \`${stat(src, 'avgSteals', stat(src, 'steals'))}\``,
        `🚫 BPG: \`${stat(src, 'avgBlocks', stat(src, 'blocks'))}\``,
        `❌ TPG: \`${stat(src, 'avgTurnovers', stat(src, 'turnovers'))}\``,
      ].join('\n'),
      inline: true,
    });
    fields.push({
      name: '🎯 Shooting',
      value: [
        `FG%: \`${stat(src, 'fieldGoalPct', stat(src, 'shootingPercentage'))}\``,
        `3P%: \`${stat(src, 'threePointFieldGoalPct', stat(src, 'threePointPct'))}\``,
        `FT%: \`${stat(src, 'freeThrowPct')}\``,
        `FGA: \`${stat(src, 'avgFieldGoalAttempts', stat(src, 'fieldGoalsAttempted'))}\``,
        `3PA: \`${stat(src, 'avgThreePointFieldGoalAttempts')}\``,
        `MPG: \`${stat(src, 'avgMinutes', stat(src, 'minutes'))}\``,
      ].join('\n'),
      inline: true,
    });
  }

  if (Object.keys(adv).length) {
    fields.push({
      name: '🧠 Advanced',
      value: [
        `PER: \`${stat(adv, 'playerEfficiencyRating', stat(adv, 'PER'))}\``,
        `TS%: \`${stat(adv, 'trueShootingPercentage', stat(adv, 'trueShootingPct'))}\``,
        `+/-: \`${stat(adv, 'plusMinus')}\``,
        `USG%: \`${stat(adv, 'usageRate', stat(adv, 'usagePct'))}\``,
        `WS: \`${stat(adv, 'winShares')}\``,
      ].join('\n'),
      inline: true,
    });
  }

  return fields;
}

// ── NFL Formatter ─────────────────────────────────────────────────────────────
function formatNFLStats(statsData, playerName, position) {
  const categories = statsData?.splits?.categories || [];
  const fields = [];

  const pos = (position || '').toUpperCase();

  // Passing
  const passing = categories.find(c => c.name === 'passing');
  if (passing) {
    const p = zipStats(passing?.stats?.[0] || passing?.totals);
    fields.push({
      name: '🏈 Passing',
      value: [
        `YDS: \`${stat(p, 'passingYards', stat(p, 'totalYards'))}\``,
        `TD: \`${stat(p, 'passingTouchdowns', stat(p, 'touchdowns'))}\``,
        `INT: \`${stat(p, 'interceptions')}\``,
        `CMP%: \`${stat(p, 'completionPct', stat(p, 'completions'))}\``,
        `RTG: \`${stat(p, 'QBRating', stat(p, 'passerRating'))}\``,
        `YPA: \`${stat(p, 'yardsPerPassAttempt')}\``,
      ].join('\n'),
      inline: true,
    });
  }

  // Rushing
  const rushing = categories.find(c => c.name === 'rushing');
  if (rushing) {
    const r = zipStats(rushing?.stats?.[0] || rushing?.totals);
    fields.push({
      name: '🏃 Rushing',
      value: [
        `YDS: \`${stat(r, 'rushingYards', stat(r, 'totalYards'))}\``,
        `ATT: \`${stat(r, 'rushingAttempts', stat(r, 'attempts'))}\``,
        `TD: \`${stat(r, 'rushingTouchdowns', stat(r, 'touchdowns'))}\``,
        `YPC: \`${stat(r, 'yardsPerRushAttempt', stat(r, 'yardsPerCarry'))}\``,
        `LNG: \`${stat(r, 'longRushing', stat(r, 'long'))}\``,
      ].join('\n'),
      inline: true,
    });
  }

  // Receiving
  const receiving = categories.find(c => c.name === 'receiving');
  if (receiving) {
    const rec = zipStats(receiving?.stats?.[0] || receiving?.totals);
    fields.push({
      name: '🙌 Receiving',
      value: [
        `YDS: \`${stat(rec, 'receivingYards', stat(rec, 'totalYards'))}\``,
        `REC: \`${stat(rec, 'receptions')}\``,
        `TD: \`${stat(rec, 'receivingTouchdowns', stat(rec, 'touchdowns'))}\``,
        `YPR: \`${stat(rec, 'yardsPerReception', stat(rec, 'yardsPerCatch'))}\``,
        `TGTS: \`${stat(rec, 'receivingTargets', stat(rec, 'targets'))}\``,
      ].join('\n'),
      inline: true,
    });
  }

  // Defense
  const defense = categories.find(c => c.name === 'defensive');
  if (defense) {
    const d = zipStats(defense?.stats?.[0] || defense?.totals);
    fields.push({
      name: '🛡️ Defense',
      value: [
        `TKL: \`${stat(d, 'totalTackles', stat(d, 'tackles'))}\``,
        `SACKS: \`${stat(d, 'sacks')}\``,
        `INT: \`${stat(d, 'interceptions')}\``,
        `PD: \`${stat(d, 'passesDefended', stat(d, 'passDeflections'))}\``,
        `FF: \`${stat(d, 'forcedFumbles')}\``,
      ].join('\n'),
      inline: true,
    });
  }

  return fields;
}

// ── MLB Formatter ─────────────────────────────────────────────────────────────
function formatMLBStats(statsData) {
  const categories = statsData?.splits?.categories || [];
  const fields = [];

  const batting = categories.find(c => c.name === 'batting' || c.displayName === 'Batting');
  if (batting) {
    const b = zipStats(batting?.stats?.[0] || batting?.totals);
    fields.push({
      name: '🏏 Batting',
      value: [
        `AVG: \`${stat(b, 'avg', stat(b, 'battingAverage'))}\``,
        `HR: \`${stat(b, 'homeRuns')}\``,
        `RBI: \`${stat(b, 'RBIs', stat(b, 'rbi'))}\``,
        `OBP: \`${stat(b, 'OBP', stat(b, 'onBasePct'))}\``,
        `SLG: \`${stat(b, 'SLG', stat(b, 'sluggingPct'))}\``,
        `OPS: \`${stat(b, 'OPS', stat(b, 'onBasePlusSlugging'))}\``,
        `H: \`${stat(b, 'hits')}\`   R: \`${stat(b, 'runs')}\``,
        `SB: \`${stat(b, 'stolenBases')}\`   SO: \`${stat(b, 'strikeouts')}\``,
      ].join('\n'),
      inline: true,
    });
  }

  const pitching = categories.find(c => c.name === 'pitching' || c.displayName === 'Pitching');
  if (pitching) {
    const p = zipStats(pitching?.stats?.[0] || pitching?.totals);
    fields.push({
      name: '⚾ Pitching',
      value: [
        `ERA: \`${stat(p, 'ERA', stat(p, 'era'))}\``,
        `W-L: \`${stat(p, 'wins')}-${stat(p, 'losses')}\``,
        `WHIP: \`${stat(p, 'WHIP', stat(p, 'whip'))}\``,
        `K: \`${stat(p, 'strikeouts')}\``,
        `IP: \`${stat(p, 'inningsPitched')}\``,
        `BB: \`${stat(p, 'walks', stat(p, 'baseOnBalls'))}\``,
        `K/9: \`${stat(p, 'strikeoutsPerNineInnings', stat(p, 'kPer9'))}\``,
        `SV: \`${stat(p, 'saves')}\``,
      ].join('\n'),
      inline: true,
    });
  }

  return fields;
}

// ── BallDontLie NBA formatter (more reliable structure) ───────────────────────
function formatBDLStats(bdlData) {
  const { player, stats, season } = bdlData;
  if (!stats) return null;

  return {
    playerName: `${player.first_name} ${player.last_name}`,
    team: player.team?.full_name || 'Unknown Team',
    position: player.position || 'N/A',
    season,
    fields: [
      {
        name: '📊 Per Game Averages',
        value: [
          `🏀 PPG: \`${(stats.pts ?? 0).toFixed(1)}\``,
          `🔄 RPG: \`${(stats.reb ?? 0).toFixed(1)}\``,
          `🎯 APG: \`${(stats.ast ?? 0).toFixed(1)}\``,
          `🛡️ SPG: \`${(stats.stl ?? 0).toFixed(1)}\``,
          `🚫 BPG: \`${(stats.blk ?? 0).toFixed(1)}\``,
          `❌ TPG: \`${(stats.turnover ?? 0).toFixed(1)}\``,
        ].join('\n'),
        inline: true,
      },
      {
        name: '🎯 Shooting',
        value: [
          `FG%: \`${stats.fg_pct != null ? (stats.fg_pct * 100).toFixed(1) + '%' : '--'}\``,
          `3P%: \`${stats.fg3_pct != null ? (stats.fg3_pct * 100).toFixed(1) + '%' : '--'}\``,
          `FT%: \`${stats.ft_pct != null ? (stats.ft_pct * 100).toFixed(1) + '%' : '--'}\``,
          `MPG: \`${stats.min ?? '--'}\``,
          `GP: \`${stats.games_played ?? '--'}\``,
        ].join('\n'),
        inline: true,
      },
    ],
  };
}

module.exports = {
  formatNBAStats,
  formatNFLStats,
  formatMLBStats,
  formatBDLStats,
};
