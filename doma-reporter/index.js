require('dotenv').config();

const { Client, GatewayIntentBits, Partials } = require('discord.js');

const stats = require('./modules/stats');
const sentiment = require('./modules/sentiment');
const triage = require('./modules/triage');
const reports = require('./modules/reports');
const reminders = require('./modules/reminders');
const collect = require('./modules/collect');
const recovery = require('./modules/recovery');
const security = require('./modules/security');
const impersonation = require('./modules/impersonation');
// const search = require('./modules/search'); // disabled — see commented init below
const whois = require('./modules/whois');
const faq = require('./modules/faq');
const cnReport = require('./modules/cn-report');
const domaLookup = require('./modules/doma-lookup');
const domaFeed = require('./modules/doma-feed');
const domaMetrics = require('./modules/doma-metrics');
const portfolio = require('./modules/portfolio');
const fantasy = require('./modules/fantasy');

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildModeration,
  ],
  partials: [Partials.GuildMember],
});

// Several modules register their own messageCreate/interactionCreate listeners.
// Default max listener limit is 10 — we have more than that.
client.setMaxListeners(30);

client.once('ready', () => {
  console.log(`[doma-reporter] Logged in as ${client.user.tag}`);

  stats.init(client);
  sentiment.init(client);
  triage.init(client);
  reports.init(client);
  reminders.init(client);
  collect.init(client);
  recovery.init(client);
  security.init(client);
  impersonation.init(client);
  cnReport.init(client);
  // search.init(client); // disabled — module was triggering full guild member fetch on every invocation
  whois.init(client);
  faq.init(client);
  domaLookup.init(client);
  domaFeed.init(client);
  domaMetrics.init(client);
  portfolio.init(client);
  fantasy.init(client);

  console.log('[doma-reporter] All modules loaded');
});

client.login(process.env.DISCORD_TOKEN);
