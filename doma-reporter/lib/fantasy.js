/**
 * Pure logic for the Fantasy League: eligibility filter, tier assignment,
 * budget computation. No I/O — fed token lists from doma.js, returns plain
 * objects that modules/fantasy/index.js writes to Supabase.
 */

const DEFAULT_FILTER = {
  min_holders: 25,
  min_volume_usd: 50,
  or_min_fdv_usd: 1000,
};

const DEFAULT_BUDGET_FACTOR = 0.35;
const TOP_N_FOR_BUDGET = 10;

const TIER_BREAKPOINTS = {
  PREMIUM: 0.05,    // top 5% by FDV
  UPPER_MID: 0.15,  // next 15% (cumulative 20%)
  MID: 0.40,        // next 40% (cumulative 60%)
  // SMALL: remaining 40%
};

function num(v) {
  if (v === null || v === undefined) return 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * @param {object} t - raw token from doma.fetchAllTokens()
 * @param {object} [filter] - filter knobs (defaults are the agreed v1 thresholds)
 * @returns {boolean}
 */
function isEligible(t, filter = DEFAULT_FILTER) {
  if (!t || !t.params || !t.params.name) return false;

  const status = t.status;
  if (status !== 'GRADUATION_SUCCESSFUL' && status !== 'FRACTIONALIZED') return false;
  if (t.boughtOutAt) return false;

  const fdv = num(t.currentFDV);
  if (fdv <= 0) return false;

  const holders = num(t.fractionalTokenHolderCount);
  if (holders < filter.min_holders) return false;

  const vol = num(t.volumeUsd);
  if (vol < filter.min_volume_usd && fdv < filter.or_min_fdv_usd) return false;

  return true;
}

/**
 * Assign display tiers to tokens already sorted by FDV desc.
 * Tier is purely cosmetic — does not gate selection (per spec).
 */
function assignTiers(sortedDesc) {
  const n = sortedDesc.length;
  if (n === 0) return [];

  const premCutoff = Math.max(1, Math.floor(n * TIER_BREAKPOINTS.PREMIUM));
  const upperCutoff = premCutoff + Math.max(1, Math.floor(n * TIER_BREAKPOINTS.UPPER_MID));
  const midCutoff = upperCutoff + Math.max(1, Math.floor(n * TIER_BREAKPOINTS.MID));

  return sortedDesc.map((t, i) => {
    let tier;
    if (i < premCutoff) tier = 'PREMIUM';
    else if (i < upperCutoff) tier = 'UPPER_MID';
    else if (i < midCutoff) tier = 'MID';
    else tier = 'SMALL';
    return { ...t, tier };
  });
}

/**
 * Budget = factor × sum(top N FDV). Computed once at season creation
 * from the week-1 snapshot, then locked for the season.
 */
function computeBudget(sortedDesc, factor = DEFAULT_BUDGET_FACTOR, topN = TOP_N_FOR_BUDGET) {
  const top = sortedDesc.slice(0, topN);
  const sum = top.reduce((s, t) => s + num(t.currentFDV), 0);
  return Math.round(sum * factor);
}

/**
 * Build a fully shaped snapshot from a raw token list.
 * Returns: { eligible: [...], tieredEligible: [...], totalMarketFdv, top10FdvSum, suggestedBudget }
 */
function buildSnapshot(rawTokens, opts = {}) {
  const filter = opts.filter || DEFAULT_FILTER;
  const factor = opts.budgetFactor ?? DEFAULT_BUDGET_FACTOR;

  const eligible = rawTokens.filter((t) => isEligible(t, filter));
  const sortedDesc = [...eligible].sort((a, b) => num(b.currentFDV) - num(a.currentFDV));
  const tieredEligible = assignTiers(sortedDesc);

  const totalMarketFdv = sortedDesc.reduce((s, t) => s + num(t.currentFDV), 0);
  const top10FdvSum = sortedDesc.slice(0, 10).reduce((s, t) => s + num(t.currentFDV), 0);
  const suggestedBudget = computeBudget(sortedDesc, factor);

  return {
    eligible: sortedDesc,
    tieredEligible,
    totalMarketFdv,
    top10FdvSum,
    suggestedBudget,
    filter,
  };
}

/**
 * Map a token (post-tier-assign) to a fantasy_pool_prices row.
 */
function toPriceRow(snapshotId, t) {
  return {
    snapshot_id: snapshotId,
    token_address: t.address,
    domain_name: t.params?.name || 'unknown',
    fdv_usd: num(t.currentFDV),
    price_usd: num(t.priceUsd),
    volume_usd: num(t.volumeUsd),
    holder_count: num(t.fractionalTokenHolderCount),
    status: t.status,
    tier: t.tier || null,
  };
}

module.exports = {
  DEFAULT_FILTER,
  DEFAULT_BUDGET_FACTOR,
  TOP_N_FOR_BUDGET,
  isEligible,
  assignTiers,
  computeBudget,
  buildSnapshot,
  toPriceRow,
};
