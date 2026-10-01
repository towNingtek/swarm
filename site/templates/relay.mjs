#!/usr/bin/env node
// dsh container 內部反代：把 0.0.0.0:RELAY_PORT 收到的 HTTP / WebSocket 轉發到
// 127.0.0.1:DSH_TARGET_PORT。
import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";
import { ensureProfile } from "./profile-init.mjs";
import path from "node:path";
import { createRelayPolicy, createLaunchLogFilter } from "./relay-policy.mjs";

const RELAY_PORT = Number(process.env.RELAY_PORT ?? "3080");
const TARGET_PORT = Number(process.env.DSH_TARGET_PORT ?? "3081");
const TARGET_HOST = "127.0.0.1";
const DSH_BIN = process.env.DSH_BIN ?? "dsh";
const DSH_HOME = process.env.DSH_HOME ?? path.join(process.env.HOME ?? "", ".dsh");
const DSH_WEB_PROFILE_SRC = process.env.DSH_WEB_PROFILE_DIR ?? "/opt/dsh-web-profile";

// Standalone localhost only by default. Production requires sitectl env wiring
// for RELAY_PUBLIC_ORIGIN + RELAY_TRUSTED_PEERS (exact immediate-peer IPs).
const applyHeaderPolicy = createRelayPolicy(process.env, { relayPort: RELAY_PORT, targetPort: TARGET_PORT });

// Validate the sealed image and complete staged copy before atomic publication.
// Unknown/older profiles fail closed, without deleting customer configuration.
const profileDir = path.join(DSH_HOME, "profiles", "web");
const profile = ensureProfile({ sourceDir: DSH_WEB_PROFILE_SRC, profileDir });
console.log(`[dsh-relay] profile ${profile.initialized ? "initialized" : "verified"}: ${profileDir} (${profile.hash})`);

const dshArgs = [
  "web",
  "--no-open",
  "--port",
  String(TARGET_PORT),
  ...(process.env.DSH_TRUSTED_HOST ? ["--trusted-host", process.env.DSH_TRUSTED_HOST] : []),
];
let launchToken = "";
const dsh = spawn(DSH_BIN, dshArgs, { env: process.env, stdio: ["ignore", "pipe", "inherit"] });
const stdoutFilter = createLaunchLogFilter({
  write: (text) => process.stdout.write(text),
  onToken: (token) => { launchToken = token; },
});
dsh.stdout.on("data", (chunk) => stdoutFilter.push(chunk));
dsh.stdout.on("end", () => stdoutFilter.end());
// Wait for stdout EOF/redaction before exiting; exit can precede pipe drain.
dsh.on("close", (code) => {
  console.log(`[dsh-relay] dsh exited with code ${code}`);
  process.exit(code ?? 1);
});

const server = http.createServer((req, res) => {
  let headers;
  try { headers = applyHeaderPolicy(req); } catch {
    res.writeHead(403, { "content-type": "text/plain" });
    res.end("relay request rejected\n");
    return;
  }
  const proxyReq = http.request(
    {
      host: TARGET_HOST,
      port: TARGET_PORT,
      method: req.method,
      path: req.url,
      headers,
    },
    (proxyRes) => {
      const responseHeaders = { ...proxyRes.headers };
      // The launch token is a bearer credential for this process and must never
      // reach a browser. web-auth now completes the native handoff server-side
      // via POST /auth/native-session, so this relay only forwards responses.
      // Fail closed: if any upstream still emits a token Location, strip it
      // rather than passing the credential through.
      const location = responseHeaders.location;
      if (typeof location === "string" && launchToken && location.includes(launchToken)) {
        responseHeaders.location = "/auth/handoff";
      }
      res.writeHead(proxyRes.statusCode, responseHeaders);
      proxyRes.pipe(res);
    }
  );
  proxyReq.on("error", (err) => {
    if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain" });
    res.end(`relay error: ${err.message}`);
  });
  req.pipe(proxyReq);
});

// WebSocket / h2c upgrade：把 upgrade 請求轉給 dsh，再雙向 pipe 原始 socket。
server.on("upgrade", (req, clientSocket, head) => {
  let headers;
  try { headers = applyHeaderPolicy(req); } catch {
    clientSocket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
    return;
  }
  const upHead = Buffer.concat([
    Buffer.from(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`),
    Buffer.from(Object.entries(headers).map(([k, v]) => `${k}: ${v}`).join("\r\n")),
    Buffer.from("\r\n\r\n"),
    head,
  ]);
  const target = net.connect(TARGET_PORT, TARGET_HOST, () => {
    target.write(upHead);
    target.pipe(clientSocket);
    clientSocket.pipe(target);
  });
  target.on("error", () => clientSocket.destroy());
  clientSocket.on("error", () => target.destroy());
});

server.listen(RELAY_PORT, "0.0.0.0", () => {
  console.log(`[dsh-relay] listening 0.0.0.0:${RELAY_PORT} -> ${TARGET_HOST}:${TARGET_PORT}`);
});