/**
 * Fantasy League cog — 10-day round model + magic-link auth.
 *
 * State-machine tick every 5 minutes:
 *   UPCOMING -> DRAFTING (snapshot prices, lock budget, open drafts)
 *   DRAFTING -> ACTIVE   (lock lineups)
 *   ACTIVE   -> COMPLETE (round closes — top 3 are announced manually
 *                         via !fantasy results for the first test)
 *
 * Operator commands (Core Team role gated, anywhere):
 *   !fantasy preview                    — eligible pool + suggested budget (read-only)
 *   !fantasy create-round-now           — round opening immediately (3d/7d default)
 *   !fantasy create-round <name>        — round with explicit timestamps
 *       opens=ISO locks=ISO resolves=ISO
 *   !fantasy tick                       — run state machine once
 *   !fantasy results <round_id>         — manual top-3 results dump
 *
 * Player commands (#original-gansters only, OG role gated):
 *   !fantasy enter                      — DMs a one-time magic link
 */

const crypto = require('crypto');
const cron = require('node-cron');
const { EmbedBuilder } = require('discord.js');
const doma = require('../../lib/doma');
const fantasy = require('../../lib/fantasy');
const supabase = require('../../lib/supabase');

// ── Discord-side gating ─────────────────────────────────────────────
const CORE_TEAM_ROLE_ID = '1220816930827665479';

// First-test gating: only #original-gansters, only OG role.
const PLAYER_CHANNEL_ID = '1483861197236342894';   // #original-gansters
const PLAYER_ROLE_ID    = '1384424770027786283';   // OG

// Winners DM their wallet to Clowes (@marclita) for Flipper points.
const CLOWES_USER_ID = '1190927147859185756';

const TICK_CRON = '*/5 * * * *';
const DOMA_CHAIN_ID = 97477;

// Module-scoped Discord client — set in init(), used by announcement helpers.
let _client = null;

// Default round shape (used by !fantasy create-round-now)
const DEFAULT_DRAFT_DAYS = 3;
const DEFAULT_SCORING_DAYS = 7;

// Magic-link config
const AUTH_CODE_TTL_MIN = 30;
const WEB3GUIDES_BASE_URL = (process.env.WEB3GUIDES_BASE_URL || 'https://web3guides.com').replace(/\/+$/, '');

function caip10(t) {
  if (!t.address) return null;
  if (String(t.address).includes(':')) return t.address;
  return `eip155:${DOMA_CHAIN_ID}:${String(t.address).toLowerCase()}`;
}

function isCoreTeam(member) {
  return member?.roles?.cache?.has(CORE_TEAM_ROLE_ID);
}
function isOG(member) {
  return member?.roles?.cache?.has(PLAYER_ROLE_ID);
}

function fmtMoney(n) {
  if (n === null || n === undefined) return '—';
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(2)}K`;
  return `$${Number(n).toFixed(2)}`;
}

function discordTs(iso, style = 'R') {
  return `<t:${Math.floor(new Date(iso).getTime() / 1000)}:${style}>`;
}

/* ─────────────────────────────────────────────
   Auto announcements (#original-gansters)
   ───────────────────────────────────────────── */

async function getAnnounceChannel() {
  if (!_client) return null;
  try {
    return await _client.channels.fetch(PLAYER_CHANNEL_ID);
  } catch (err) {
    console.error('[fantasy] could not fetch announce channel:', err.message);
    return null;
  }
}

async function announceRoundOpen(round, snapInfo) {
  const ch = await getAnnounceChannel();
  if (!ch) return;
  const eligible = snapInfo?.eligible ?? '—';
  const budget = Number(snapInfo?.budget_usd ?? round.budget_usd ?? 0);

  const embed = new EmbedBuilder()
    .setColor(0x7c6aff)
    .setTitle('🎯  Doma Fantasy League — Test Round Open')
    .setDescription(
      `**${round.name}** is live.\n` +
      `Build a 10-domain portfolio under budget. Top finishers earn Flipper points.`
    )
    .addFields(
      { name: 'Eligible pool', value: `${eligible} domains`, inline: true },
      { name: 'Your budget', value: fmtMoney(budget), inline: true },
      { name: 'Pick count', value: '10', inline: true },
      { name: 'Draft locks', value: discordTs(round.draft_locks_at, 'R'), inline: true },
      { name: 'Round resolves', value: discordTs(round.resolves_at, 'R'), inline: true },
      { name: '​', value: '​', inline: true },
      {
        name: 'How to enter',
        value:
          '• Run `!fantasy enter` in this channel\n' +
          '• Bot DMs you a one-click magic link\n' +
          '• Pick 10 domains, lock in, done.\n' +
          '_OG role only for this test._',
      }
    )
    .setFooter({ text: 'No wallet needed to play. Winners send wallet to @marclita.' });

  try {
    await ch.send({ embeds: [embed] });
  } catch (err) {
    console.error('[fantasy] announceRoundOpen failed:', err.message);
  }
}

async function announceLineupsLocked(round) {
  const ch = await getAnnounceChannel();
  if (!ch) return;

  // Count teams in play
  let teamCount = 0;
  try {
    const { data } = await supabase
      .from('fantasy_holdings')
      .select('discord_id')
      .eq('round_id', round.round_id);
    teamCount = new Set((data || []).map((r) => r.discord_id)).size;
  } catch {}

  const embed = new EmbedBuilder()
    .setColor(0x5eead4)
    .setTitle('🔒  Lineups locked — scoring begins.')
    .setDescription(
      `**${round.name}** · ${teamCount} team${teamCount === 1 ? '' : 's'} in play.\n` +
      `No swaps from here. Held positions tracked against live FDV until resolution.`
    )
    .addFields(
      { name: 'Round resolves', value: discordTs(round.resolves_at, 'R'), inline: true },
      { name: 'Track live', value: '[web3guides.com/fantasy/leaderboard](https://web3guides.com/fantasy/leaderboard)', inline: true },
    );

  try {
    await ch.send({ embeds: [embed] });
  } catch (err) {
    console.error('[fantasy] announceLineupsLocked failed:', err.message);
  }
}

async function announceWinners(round) {
  const ch = await getAnnounceChannel();
  if (!ch) return;

  let results;
  try {
    results = await computeResults(round.round_id, 10);
  } catch (err) {
    console.error('[fantasy] computeResults failed in announceWinners:', err.message);
    return;
  }
  if (!results.ranked.length) {
    try {
      await ch.send({
        embeds: [new EmbedBuilder()
          .setColor(0xf87171)
          .setTitle(`${round.name} — no teams`)
          .setDescription('No holdings were locked in for this round.')],
      });
    } catch {}
    return;
  }

  const guild = ch.guild;
  const resolveName = async (id) => {
    try {
      const m = await guild?.members.fetch(id);
      return m?.user?.username || m?.displayName || id;
    } catch {
      return id;
    }
  };

  const top3 = results.ranked.slice(0, 3);
  const rest = results.ranked.slice(3, 10);
  const medals = ['🥇', '🥈', '🥉'];

  const top3Lines = await Promise.all(
    top3.map(async (r, i) => {
      const name = await resolveName(r.discord_id);
      const sign = r.pct_growth >= 0 ? '+' : '';
      return `${medals[i]}  **${name}**  —  ${fmtMoney(r.total_portfolio_usd)}  (${sign}${r.pct_growth.toFixed(2)}%)`;
    })
  );

  const restLines = await Promise.all(
    rest.map(async (r, i) => {
      const name = await resolveName(r.discord_id);
      const sign = r.pct_growth >= 0 ? '+' : '';
      const rank = (i + 4).toString().padStart(2, ' ');
      return `\`${rank}.\` ${name.padEnd(20)}  ${fmtMoney(r.total_portfolio_usd).padStart(9)}  (${sign}${r.pct_growth.toFixed(2)}%)`;
    })
  );

  const winnerMentions = top3.map((r) => `<@${r.discord_id}>`).join(' · ');

  const embed = new EmbedBuilder()
    .setColor(0xf0b340)
    .setTitle(`🏆  ${round.name} — Final Results`)
    .setDescription(
      `Starting budget: ${fmtMoney(Number(results.budget))}  ·  ${results.ranked.length} team${results.ranked.length === 1 ? '' : 's'} ranked`
    )
    .addFields(
      { name: 'Top 3', value: top3Lines.join('\n') },
      ...(restLines.length ? [{ name: 'Top 10', value: '```\n' + restLines.join('\n') + '\n```' }] : []),
      {
        name: 'Winners — claim your Flipper points',
        value:
          `${winnerMentions}\n\n` +
          `**Please DM your wallet address to <@${CLOWES_USER_ID}> (@marclita).**\n` +
          `Points will be pushed manually to whatever address you send. No wallet needed to play next round.`,
      }
    )
    .setFooter({ text: 'GG. Next round opens soon.' });

  try {
    // Send the winner pings as a separate message so they actually ping
    // (embed-only mentions don't notify users).
    await ch.send({
      content: `🏆  Winners: ${winnerMentions}`,
      embeds: [embed],
      allowedMentions: { users: top3.map((r) => r.discord_id) },
    });
  } catch (err) {
    console.error('[fantasy] announceWinners failed:', err.message);
  }
}

/* ─────────────────────────────────────────────
   Snapshot persistence
   ───────────────────────────────────────────── */

async function takeSnapshotForRound(round) {
  doma.invalidateCache();
  const allTokens = await doma.fetchAllTokens();
  const snap = fantasy.buildSnapshot(allTokens, { filter: round.pool_filter });

  const { data: snapRow, error: snapErr } = await supabase
    .from('fantasy_pool_snapshots')
    .insert({
      round_id: round.round_id,
      snapshot_at: new Date().toISOString(),
      total_market_fdv_usd: snap.totalMarketFdv,
      top10_fdv_sum_usd: snap.top10FdvSum,
      eligible_count: snap.eligible.length,
    })
    .select()
    .single();
  if (snapErr) throw snapErr;

  const priceRows = snap.tieredEligible
    .map((t) => ({ ...fantasy.toPriceRow(snapRow.snapshot_id, t), token_address: caip10(t) }))
    .filter((r) => r.token_address);

  const { error: priceErr } = await supabase
    .from('fantasy_pool_prices')
    .insert(priceRows);
  if (priceErr) throw priceErr;

  return { snapshot_id: snapRow.snapshot_id, ...snap };
}

/* ─────────────────────────────────────────────
   State-machine tick
   ───────────────────────────────────────────── */

async function runStateTick(now = new Date()) {
  const nowIso = now.toISOString();
  const events = [];

  // 1) UPCOMING -> DRAFTING
  const { data: opening, error: openErr } = await supabase
    .from('fantasy_rounds')
    .select('*')
    .eq('status', 'UPCOMING')
    .lte('draft_opens_at', nowIso);
  if (openErr) throw openErr;

  for (const round of opening || []) {
    try {
      const snap = await takeSnapshotForRound(round);
      const updates = { status: 'DRAFTING' };
      if (!round.budget_usd || Number(round.budget_usd) <= 0) {
        updates.budget_usd = Math.round(snap.suggestedBudget);
      }
      const { error: updErr } = await supabase
        .from('fantasy_rounds')
        .update(updates)
        .eq('round_id', round.round_id);
      if (updErr) throw updErr;
      const event = {
        type: 'opened',
        round_id: round.round_id,
        eligible: snap.eligible.length,
        budget_usd: Number(updates.budget_usd ?? round.budget_usd),
      };
      events.push(event);
      // Announce — best-effort, don't block the state machine on Discord errors.
      announceRoundOpen({ ...round, ...updates }, event).catch((e) =>
        console.error('[fantasy] announce open error:', e.message)
      );
    } catch (err) {
      events.push({ type: 'open_failed', round_id: round.round_id, error: String(err.message || err) });
    }
  }

  // 2) DRAFTING -> ACTIVE
  const { data: locking } = await supabase
    .from('fantasy_rounds')
    .select('*')
    .eq('status', 'DRAFTING')
    .lte('draft_locks_at', nowIso);
  for (const round of locking || []) {
    const { error } = await supabase
      .from('fantasy_rounds')
      .update({ status: 'ACTIVE' })
      .eq('round_id', round.round_id);
    if (error) {
      events.push({ type: 'lock_failed', round_id: round.round_id, error: String(error.message) });
    } else {
      events.push({ type: 'locked', round_id: round.round_id });
      announceLineupsLocked(round).catch((e) =>
        console.error('[fantasy] announce locked error:', e.message)
      );
    }
  }

  // 3) ACTIVE -> COMPLETE
  const { data: resolving } = await supabase
    .from('fantasy_rounds')
    .select('*')
    .eq('status', 'ACTIVE')
    .lte('resolves_at', nowIso);
  for (const round of resolving || []) {
    const { error } = await supabase
      .from('fantasy_rounds')
      .update({ status: 'COMPLETE' })
      .eq('round_id', round.round_id);
    if (error) {
      events.push({ type: 'resolve_failed', round_id: round.round_id, error: String(error.message) });
    } else {
      events.push({ type: 'resolved', round_id: round.round_id });
      announceWinners(round).catch((e) =>
        console.error('[fantasy] announce winners error:', e.message)
      );
    }
  }

  return events;
}

/* ─────────────────────────────────────────────
   Preview / round creation
   ───────────────────────────────────────────── */

async function runPreview({ filter } = {}) {
  doma.invalidateCache();
  const allTokens = await doma.fetchAllTokens();
  const snap = fantasy.buildSnapshot(allTokens, { filter });
  return {
    eligible_count: snap.eligible.length,
    total_market_fdv_usd: snap.totalMarketFdv,
    top10_fdv_sum_usd: snap.top10FdvSum,
    suggested_budget_usd: snap.suggestedBudget,
    top10: snap.tieredEligible.slice(0, 10).map((t) => ({
      domain: t.params?.name,
      fdv_usd: Number(t.currentFDV),
      tier: t.tier,
    })),
  };
}

async function createRoundNow({ name } = {}) {
  const now = new Date();
  const drafts = new Date(now.getTime() + DEFAULT_DRAFT_DAYS * 86_400_000);
  const resolves = new Date(now.getTime() + (DEFAULT_DRAFT_DAYS + DEFAULT_SCORING_DAYS) * 86_400_000);
  return insertRound({
    name: name || `Round ${now.toISOString().slice(0, 10)}`,
    draft_opens_at: now.toISOString(),
    draft_locks_at: drafts.toISOString(),
    resolves_at: resolves.toISOString(),
  });
}

async function insertRound({ name, draft_opens_at, draft_locks_at, resolves_at }) {
  const { data, error } = await supabase
    .from('fantasy_rounds')
    .insert({
      name,
      draft_opens_at,
      draft_locks_at,
      resolves_at,
      budget_usd: 0, // back-filled at snapshot time
      status: 'UPCOMING',
    })
    .select()
    .single();
  if (error) throw error;
  return data;
}

/**
 * Parse `key=value` args from a Discord message into { name, opens, locks, resolves }.
 * Accepts ISO 8601 in UTC (e.g. 2026-05-11T14:00:00Z).
 */
function parseRoundArgs(parts) {
  const out = {};
  for (const p of parts.slice(2)) {
    const eq = p.indexOf('=');
    if (eq < 0) continue;
    const k = p.slice(0, eq).toLowerCase();
    const v = p.slice(eq + 1).trim();
    out[k] = v;
  }
  return out;
}

/* ─────────────────────────────────────────────
   Manual results (used by operator until the
   automated winners flow ships)
   ───────────────────────────────────────────── */

async function computeResults(roundId, topN = 10) {
  // Pull round + snapshot
  const { data: round, error: rErr } = await supabase
    .from('fantasy_rounds').select('*').eq('round_id', roundId).single();
  if (rErr || !round) throw new Error(`Round ${roundId} not found`);

  const budget = Number(round.budget_usd || 0);

  // Pull all holdings for this round
  const { data: holdings, error: hErr } = await supabase
    .from('fantasy_holdings').select('*').eq('round_id', roundId);
  if (hErr) throw hErr;
  if (!holdings || holdings.length === 0) {
    return { round, budget, ranked: [], reason: 'no-holdings' };
  }

  // Get live FDV for every unique token in the holdings
  doma.invalidateCache();
  const tokens = await doma.fetchAllTokens();
  const fdvByAddr = new Map();
  for (const t of tokens) {
    fdvByAddr.set(caip10(t), Number(t.currentFDV || 0));
  }

  // Group holdings by user
  const byUser = new Map();
  for (const h of holdings) {
    const arr = byUser.get(h.discord_id) || [];
    arr.push(h);
    byUser.set(h.discord_id, arr);
  }

  const ranked = [];
  for (const [discordId, arr] of byUser.entries()) {
    let holdingsValue = 0;
    let totalCostBasis = 0;
    for (const h of arr) {
      const liveFdv = fdvByAddr.get(h.token_address) || Number(h.cost_basis_fdv_usd);
      holdingsValue += liveFdv;
      totalCostBasis += Number(h.cost_basis_fdv_usd);
    }
    const unspent = budget - totalCostBasis;
    const totalPortfolio = holdingsValue + unspent;
    const pctGrowth = budget > 0 ? ((totalPortfolio - budget) / budget) * 100 : 0;
    ranked.push({
      discord_id: discordId,
      picks: arr.length,
      cost_basis_usd: totalCostBasis,
      unspent_usd: unspent,
      holdings_value_usd: holdingsValue,
      total_portfolio_usd: totalPortfolio,
      pct_growth: pctGrowth,
    });
  }

  ranked.sort((a, b) => b.total_portfolio_usd - a.total_portfolio_usd);
  return { round, budget, ranked: ranked.slice(0, topN) };
}

/* ─────────────────────────────────────────────
   Magic-link issuance
   ───────────────────────────────────────────── */

function newAuthCode() {
  // 32 bytes -> 43-char URL-safe base64
  return crypto.randomBytes(32).toString('base64')
    .replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
}

async function issueMagicLink(discordUser) {
  // Invalidate prior unused codes for this user
  await supabase
    .from('fantasy_auth_codes')
    .update({ used_at: new Date().toISOString() })
    .eq('discord_id', discordUser.id)
    .is('used_at', null);

  const code = newAuthCode();
  const expiresAt = new Date(Date.now() + AUTH_CODE_TTL_MIN * 60_000).toISOString();
  const { error } = await supabase
    .from('fantasy_auth_codes')
    .insert({ code, discord_id: discordUser.id, expires_at: expiresAt });
  if (error) throw error;

  return {
    code,
    url: `${WEB3GUIDES_BASE_URL}/fantasy/enter?code=${code}`,
    expires_at: expiresAt,
  };
}

/* ─────────────────────────────────────────────
   Cog wiring
   ───────────────────────────────────────────── */

function init(client) {
  _client = client;

  client.on('messageCreate', async (msg) => {
    if (msg.author.bot) return;
    const content = (msg.content || '').trim();
    if (!content.startsWith('!fantasy')) return;

    const parts = content.split(/\s+/);
    const sub = (parts[1] || '').toLowerCase();

    // ─── Player commands: channel + OG-role gated ───────────
    if (sub === 'enter') {
      if (msg.channel.id !== PLAYER_CHANNEL_ID) return; // silent ignore outside the test channel
      if (!isOG(msg.member) && !isCoreTeam(msg.member)) {
        try { await msg.reply('Fantasy League is OG-only for the first test.'); } catch {}
        return;
      }
      try {
        const link = await issueMagicLink(msg.author);
        let dmOk = true;
        try {
          await msg.author.send(
            `🎟️  **Doma Fantasy League — your access link**\n\n` +
            `Click to enter (expires in ${AUTH_CODE_TTL_MIN} min, one-time use):\n${link.url}\n\n` +
            `After you click it, the page remembers you for 24h — no need to run \`!fantasy enter\` again unless you log out or use a new device.`
          );
        } catch {
          dmOk = false;
        }
        if (dmOk) {
          try { await msg.reply('Check your DMs — link sent.'); } catch {}
        } else {
          try { await msg.reply('Could not DM you. Open your DM settings for this server, then try again.'); } catch {}
        }
      } catch (err) {
        console.error('[fantasy] enter failed:', err);
        try { await msg.reply(`Error issuing your link. Ping a Core Team member. \`${err.message}\``); } catch {}
      }
      return;
    }

    // ─── Operator commands: Core Team only ──────────────────
    if (!isCoreTeam(msg.member)) return;

    try {
      if (sub === 'preview') {
        await msg.reply('Building preview…');
        const result = await runPreview();
        const lines = [
          `eligible: ${result.eligible_count}`,
          `total mkt FDV: ${fmtMoney(result.total_market_fdv_usd)}`,
          `top-10 FDV sum: ${fmtMoney(result.top10_fdv_sum_usd)}`,
          `suggested budget (35% of top-10): ${fmtMoney(result.suggested_budget_usd)}`,
          '',
          'top 10:',
          ...result.top10.map(
            (t, i) =>
              `  ${(i + 1).toString().padStart(2)}. ${(t.domain || '').padEnd(24)} ${fmtMoney(t.fdv_usd).padStart(10)}  [${t.tier}]`
          ),
        ];
        await msg.reply('```\n' + lines.join('\n') + '\n```');
      } else if (sub === 'create-round-now') {
        await msg.reply('Creating round opening now…');
        const round = await createRoundNow({});
        const tickEvents = await runStateTick();
        await msg.reply('```\n' + JSON.stringify({ round, tick: tickEvents }, null, 2) + '\n```');
      } else if (sub === 'create-round') {
        // Usage: !fantasy create-round name=test-1 opens=2026-05-11T14:00:00Z locks=... resolves=...
        const args = parseRoundArgs(parts);
        const required = ['name', 'opens', 'locks', 'resolves'];
        const missing = required.filter((k) => !args[k]);
        if (missing.length) {
          await msg.reply(
            'Usage: `!fantasy create-round name=<n> opens=<iso> locks=<iso> resolves=<iso>`\n' +
            `Missing: ${missing.join(', ')}`
          );
          return;
        }
        const round = await insertRound({
          name: args.name,
          draft_opens_at: new Date(args.opens).toISOString(),
          draft_locks_at: new Date(args.locks).toISOString(),
          resolves_at: new Date(args.resolves).toISOString(),
        });
        const tickEvents = await runStateTick();
        await msg.reply('```\n' + JSON.stringify({ round, tick: tickEvents }, null, 2) + '\n```');
      } else if (sub === 'tick') {
        const events = await runStateTick();
        await msg.reply('```\n' + JSON.stringify(events, null, 2) + '\n```');
      } else if (sub === 'results') {
        const roundId = parseInt(parts[2], 10);
        if (!Number.isFinite(roundId)) {
          await msg.reply('Usage: `!fantasy results <round_id>`');
          return;
        }
        const { round, budget, ranked, reason } = await computeResults(roundId);
        if (!ranked.length) {
          await msg.reply(`No holdings for round ${roundId} (${reason || 'unknown'}).`);
          return;
        }
        const guild = msg.guild;
        const lines = await Promise.all(
          ranked.map(async (r, i) => {
            let name = r.discord_id;
            try {
              const m = await guild?.members.fetch(r.discord_id);
              name = m?.user?.username || m?.displayName || r.discord_id;
            } catch {}
            const sign = r.pct_growth >= 0 ? '+' : '';
            return `  ${(i + 1).toString().padStart(2)}. ${name.padEnd(22)} ` +
                   `${fmtMoney(r.total_portfolio_usd).padStart(10)}  ` +
                   `(${sign}${r.pct_growth.toFixed(2)}%)`;
          })
        );
        await msg.reply(
          `**${round.name}** — top ${ranked.length}\n` +
          `Budget: ${fmtMoney(budget)} · Status: ${round.status}\n` +
          '```\n' + lines.join('\n') + '\n```'
        );
      } else if (sub === 'announce-winners') {
        const roundId = parseInt(parts[2], 10);
        if (!Number.isFinite(roundId)) {
          await msg.reply('Usage: `!fantasy announce-winners <round_id>`');
          return;
        }
        const { data: round, error } = await supabase
          .from('fantasy_rounds').select('*').eq('round_id', roundId).single();
        if (error || !round) {
          await msg.reply(`Round ${roundId} not found.`);
          return;
        }
        await announceWinners(round);
        await msg.reply(`Winners post sent to <#${PLAYER_CHANNEL_ID}>.`);
      } else {
        await msg.reply(
          '**Operator commands:**\n' +
          '`!fantasy preview` — eligible pool + suggested budget (read-only)\n' +
          '`!fantasy create-round-now` — round opening immediately (3d/7d default)\n' +
          '`!fantasy create-round name=X opens=ISO locks=ISO resolves=ISO` — explicit timestamps\n' +
          '`!fantasy tick` — run the state machine once\n' +
          '`!fantasy results <round_id>` — manual top-10 ranking (no announce)\n' +
          '`!fantasy announce-winners <round_id>` — force-post winners to #original-gansters\n\n' +
          '**Player commands:**\n' +
          '`!fantasy enter` (in #original-gansters, OG role) — DM yourself a magic link'
        );
      }
    } catch (err) {
      console.error('[fantasy] command failed:', err);
      try { await msg.reply(`Error: \`${err.message || err}\``); } catch {}
    }
  });

  cron.schedule(
    TICK_CRON,
    async () => {
      try {
        const events = await runStateTick();
        if (events.length > 0) console.log('[fantasy] tick events:', events);
      } catch (err) {
        console.error('[fantasy] tick failed:', err);
      }
    },
    { timezone: 'UTC' }
  );

  console.log('[fantasy] initialized — state-machine tick every 5 min UTC');
}

module.exports = {
  init,
  runStateTick,
  runPreview,
  createRoundNow,
  insertRound,
  computeResults,
  issueMagicLink,
  takeSnapshotForRound,
};
