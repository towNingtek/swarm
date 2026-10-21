// Runs INSIDE a customer site container (docker exec -i ... node -), CommonJS.
// Used by platform/support_site_line.py (opt-in: PLATFORM_SITE_LINE=1).
//
// The platform never reads a customer's LINE or Discord token. It pipes this
// script plus one JSON request on stdin; the script reads the token file the
// site's own configuration names, calls the provider, and prints ONE JSON line
// with a status. Tokens, secrets and message bodies are never printed.
//
// stdin is this script with one line prepended by the platform:
//   const __REQ = {...json...};
// (`node -` consumes all of stdin as the program, so the request travels in
// the program text; it never appears on a command line or in the process list.)
// The request carries: op, keys_dir (absolute, inside the site), and op fields.
//
//   push      {token_file, to, text}                  LINE push (15 s timeout)
//   quota     {token_file}                            {limit, used} this month
//   check     {token_file}                            bot info; sends nothing
//   discord   {token_file, channel_id, text}          one Discord message
//
// Every file name is resolved under keys_dir and must stay inside it; symlinks
// that escape are refused.
const fs = require('node:fs');
const path = require('node:path');

const TIMEOUT_MS = 15000;

function out(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }

function readKey(keysDir, name) {
  if (typeof name !== 'string' || !name || name.includes('\0') || path.isAbsolute(name) ||
      name.split(/[\\/]/).includes('..')) {
    throw new Error('bad key name');
  }
  const root = fs.realpathSync(keysDir);
  const full = fs.realpathSync(path.join(root, name));
  if (full !== root && !full.startsWith(root + path.sep)) throw new Error('key outside keys dir');
  const st = fs.statSync(full);
  if (!st.isFile() || st.size > 8192) throw new Error('bad key file');
  const value = fs.readFileSync(full, 'utf8').trim();
  if (!value) throw new Error('empty key file');
  return value;
}

async function call(url, init) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { ...init, signal: ctl.signal });
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    return { status: res.status, body };
  } catch (e) {
    return { status: 0, error: e.name === 'AbortError' ? 'timeout' : 'network' };
  } finally {
    clearTimeout(timer);
  }
}

function lineError(r) {
  if (r.status === 0) return r.error;
  const msg = r.body && typeof r.body.message === 'string' ? r.body.message.slice(0, 120) : '';
  // 429 with this message is LINE's monthly limit (not a rate limit).
  if (r.status === 429 && /monthly limit/i.test(msg)) return 'monthly_limit';
  return `http_${r.status}` + (msg ? `: ${msg}` : '');
}

async function main() {
  // eslint-disable-next-line no-undef
  const req = typeof __REQ === 'object' && __REQ ? __REQ : {};
  const keys = req.keys_dir;
  const auth = (name) => ({ Authorization: 'Bearer ' + readKey(keys, name) });
  const json = { 'Content-Type': 'application/json' };

  switch (req.op) {
    case 'push': {
      const r = await call('https://api.line.me/v2/bot/message/push', {
        method: 'POST', headers: { ...auth(req.token_file), ...json },
        body: JSON.stringify({ to: req.to, messages: [{ type: 'text', text: req.text }] }),
      });
      return out(r.status >= 200 && r.status < 300 ? { ok: true } : { ok: false, error: lineError(r) });
    }
    case 'quota': {
      const h = auth(req.token_file);
      const [q, c] = await Promise.all([
        call('https://api.line.me/v2/bot/message/quota', { headers: h }),
        call('https://api.line.me/v2/bot/message/quota/consumption', { headers: h }),
      ]);
      if (q.status !== 200 || c.status !== 200) return out({ ok: false, error: lineError(q.status !== 200 ? q : c) });
      const limit = q.body && q.body.type === 'limited' ? q.body.value : null;
      return out({ ok: true, limit, used: c.body ? c.body.totalUsage : null });
    }
    case 'check': {
      const r = await call('https://api.line.me/v2/bot/info', { headers: auth(req.token_file) });
      return out(r.status === 200 ? { ok: true, bot: r.body && r.body.basicId ? 'present' : 'unknown' }
                                  : { ok: false, error: lineError(r) });
    }
    case 'discord': {
      const r = await call(`https://discord.com/api/v10/channels/${encodeURIComponent(req.channel_id)}/messages`, {
        method: 'POST',
        headers: { Authorization: 'Bot ' + readKey(keys, req.token_file), ...json,
                   'User-Agent': 'DiscordBot (https://github.com/towNingtek/swarm, 1)' },
        body: JSON.stringify({ content: String(req.text).slice(0, 1900), allowed_mentions: { parse: [] } }),
      });
      return out(r.status >= 200 && r.status < 300 ? { ok: true } : { ok: false, error: r.status ? `http_${r.status}` : r.error });
    }
    default:
      return out({ ok: false, error: 'unknown op' });
  }
}

// Error text may carry a path; report only a coarse class, never the message.
main().catch((e) => out({ ok: false, error: 'script: ' + (/outside|bad key/.test(String(e && e.message)) ? String(e.message) : (e && e.code) || 'error') }));
