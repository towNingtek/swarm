import { isIP } from "node:net";
import { StringDecoder } from "node:string_decoder";

const normalizePeer = (ip) => ip?.startsWith("::ffff:") && isIP(ip.slice(7)) === 4 ? ip.slice(7) : ip;
const deny = () => { throw new Error("relay request rejected by header policy"); };

// Standalone defaults are NOT production readiness. sitectl must explicitly wire
// RELAY_PUBLIC_ORIGIN and RELAY_TRUSTED_PEERS; never derive trust from headers.
export function createRelayPolicy(env = {}, { relayPort = 3080, targetPort = 3081 } = {}) {
  const origin = env.RELAY_PUBLIC_ORIGIN;
  const peers = env.RELAY_TRUSTED_PEERS;
  if (Boolean(origin) !== Boolean(peers)) throw new Error("RELAY_PUBLIC_ORIGIN and RELAY_TRUSTED_PEERS must be set together");
  let publicUrl;
  let trustedPeers = new Set();
  if (origin) {
    try { publicUrl = new URL(origin); } catch { throw new Error("invalid RELAY_PUBLIC_ORIGIN"); }
    if (publicUrl.protocol !== "https:" || publicUrl.username || publicUrl.password || publicUrl.search || publicUrl.hash || publicUrl.pathname !== "/" ||
        !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/i.test(publicUrl.hostname) || isIP(publicUrl.hostname) ||
        !/^https:\/\/[^/?#]+\/?$/i.test(origin)) throw new Error("RELAY_PUBLIC_ORIGIN must be an HTTPS DNS origin");
    const entries = peers.split(",").map((p) => p.trim());
    if (entries.some((p) => !isIP(p))) throw new Error("RELAY_TRUSTED_PEERS requires comma-separated exact IP addresses");
    trustedPeers = new Set(entries.map(normalizePeer));
  }
  const hosts = publicUrl ? new Set([publicUrl.host]) : new Set(
    ["localhost", "127.0.0.1", "[::1]"].flatMap((host) => [host, `${host}:${relayPort}`, `${host}:${targetPort}`])
  );
  return function applyRelayPolicy(req) {
    const headers = {};
    const realIp = req.headers["x-real-ip"];
    for (const [key, value] of Object.entries(req.headers)) {
      const name = key.toLowerCase();
      if (name === "forwarded" || name === "cf-visitor" || name.startsWith("x-forwarded-") || name === "x-real-ip" || name === "cf-connecting-ip") continue;
      headers[name] = value;
    }
    if (req.rawHeaders) {
      let count = 0;
      for (let i = 0; i < req.rawHeaders.length; i += 2) if (req.rawHeaders[i].toLowerCase() === "host") count++;
      if (count !== 1) deny();
    }
    if (typeof headers.host !== "string" || !hosts.has(headers.host.toLowerCase())) deny();
    if (publicUrl) {
      if (!trustedPeers.has(normalizePeer(req.socket?.remoteAddress))) deny();
      headers.host = publicUrl.host;
      headers["x-forwarded-host"] = publicUrl.host;
      headers["x-forwarded-proto"] = "https";
      // The trusted peer is the platform's nginx, which overwrites X-Real-IP
      // with the visitor's address. Hand web-auth exactly that one address as
      // X-Forwarded-For, so its login limiter is per visitor instead of one
      // bucket for everybody (every request reaches web-auth from loopback).
      // Anything else (a list, a hostname, a missing header) is not passed on.
      if (typeof realIp === "string" && isIP(realIp.trim())) headers["x-forwarded-for"] = normalizePeer(realIp.trim());
    }
    // Origin is deliberately untouched: upstream owns HTTP and WS origin checks.
    return headers;
  };
}

// Never emit partial lines: token= and its value may span arbitrary UTF-8 chunks.
// Oversized lines are discarded, not partially emitted (including at EOF).
export function createLaunchLogFilter({ write, onToken, maxLineLength = 65536 }) {
  const decoder = new StringDecoder("utf8");
  let pending = "";
  let discarding = false;
  function line(text) {
    const match = text.match(/dsh web: .*?[?&]token=([A-Za-z0-9_-]+)/);
    if (match) onToken(match[1]);
    write(text.replace(/([?&]token=)[^\s&#]*/gi, "$1[REDACTED]"));
  }
  function consume(text) {
    for (const part of text.match(/[^\n]*\n|[^\n]+$/g) ?? []) {
      const complete = part.endsWith("\n");
      if (!discarding && pending.length + part.length > maxLineLength) {
        pending = "";
        discarding = true;
      }
      if (!discarding) pending += part;
      if (complete) {
        if (discarding) write("[dsh-relay] oversized stdout line suppressed\n");
        else line(pending);
        pending = "";
        discarding = false;
      }
    }
  }
  return {
    push(chunk) { consume(decoder.write(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))); },
    end() {
      consume(decoder.end());
      if (discarding) write("[dsh-relay] oversized stdout line suppressed\n");
      else if (pending) line(pending);
      pending = "";
      discarding = false;
    },
  };
}
