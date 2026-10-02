import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRelayPolicy, createLaunchLogFilter } from "./relay-policy.mjs";

const config = { RELAY_PUBLIC_ORIGIN: "https://app.example.com", RELAY_TRUSTED_PEERS: "192.0.2.1,::1" };
const request = (host = "app.example.com", peer = "192.0.2.1") => ({
  headers: { host, origin: "https://evil.example", "cf-visitor": '{"scheme":"https"}',
    forwarded: "host=evil.example;proto=https", "x-forwarded-proto": "javascript", "x-forwarded-host": "evil.example", "x-forwarded-for": "127.0.0.1" },
  rawHeaders: ["Host", host], socket: { remoteAddress: peer },
});

test("standalone strips all spoofed proxy claims and preserves Origin", () => {
  const input = request("localhost:3080", "198.51.100.1");
  assert.deepEqual(createRelayPolicy()(input), { host: "localhost:3080", origin: "https://evil.example" });
  assert.equal(input.headers["x-forwarded-proto"], "javascript");
  for (const host of ["localhost", "127.0.0.1:3080", "[::1]:3081"]) assert.equal(createRelayPolicy()(request(host)).host, host);
  for (const host of ["evil.example", "localhost.evil.example", "localhost:99", "localhost,evil.example", "localhost ", ""]) assert.throws(() => createRelayPolicy()(request(host)));
});

test("public scheme is configured, never inferred, with exact normalized immediate peer", () => {
  const apply = createRelayPolicy(config);
  for (const peer of ["192.0.2.1", "::ffff:192.0.2.1", "::1"]) {
    const headers = apply(request(undefined, peer));
    assert.deepEqual(headers, { host: "app.example.com", origin: "https://evil.example", "x-forwarded-proto": "https", "x-forwarded-host": "app.example.com" });
  }
  for (const peer of ["192.0.2.2", "127.0.0.1", undefined]) {
    const input = request(); input.socket.remoteAddress = peer;
    assert.throws(() => apply(input));
  }
  for (const host of ["evil.example", "app.example.com.evil.example", "app.example.com:80", ["app.example.com"]]) assert.throws(() => apply(request(host)));
  const duplicate = request(); duplicate.rawHeaders.push("hOsT", "evil.example");
  assert.throws(() => apply(duplicate));
  const missing = request(); missing.rawHeaders = []; assert.throws(() => apply(missing));
});

test("the visitor address from the trusted proxy reaches web-auth, nothing else does", () => {
  const apply = createRelayPolicy(config);
  for (const [realIp, expected] of [["203.0.113.7", "203.0.113.7"], [" 2001:db8::1 ", "2001:db8::1"],
    ["::ffff:203.0.113.7", "203.0.113.7"], ["203.0.113.7, 10.0.0.1", undefined], ["evil.example", undefined],
    [["203.0.113.7", "1.2.3.4"], undefined], [undefined, undefined]]) {
    const input = request();
    if (realIp === undefined) delete input.headers["x-real-ip"]; else input.headers["x-real-ip"] = realIp;
    assert.equal(apply(input)["x-forwarded-for"], expected, String(realIp));
    assert.equal(apply(input)["x-real-ip"], undefined);
  }
  // Standalone (no trusted proxy configured): never trusted.
  const local = request("localhost:3080"); local.headers["x-real-ip"] = "203.0.113.7";
  assert.equal(createRelayPolicy()(local)["x-forwarded-for"], undefined);
});

test("invalid or partial public settings fail closed", () => {
  for (const env of [{ RELAY_PUBLIC_ORIGIN: config.RELAY_PUBLIC_ORIGIN }, { RELAY_TRUSTED_PEERS: "192.0.2.1" },
    ...["http://app.example.com", "https://127.0.0.1", "https://[::1]", "https://localhost", "https://user@app.example.com", "https://app.example.com/path", "https://app.example.com?x", "https://app.example.com#x"].map(RELAY_PUBLIC_ORIGIN => ({ ...config, RELAY_PUBLIC_ORIGIN })),
    ...["*", "192.0.2.0/24", "localhost", "192.0.2.1,"].map(RELAY_TRUSTED_PEERS => ({ ...config, RELAY_TRUSTED_PEERS }))]) assert.throws(() => createRelayPolicy(env));
});

test("HTTP and upgrade invoke the same policy without rewriting Origin", () => {
  const source = readFileSync(new URL("./relay.mjs", import.meta.url), "utf8");
  assert.equal(source.match(/headers = applyHeaderPolicy\(req\)/g)?.length, 2);
  assert.ok(source.slice(source.indexOf('server.on("upgrade"')).includes("headers = applyHeaderPolicy(req)"));
  const apply = createRelayPolicy(config);
  const http = request(); const ws = request(); ws.headers.upgrade = "websocket";
  const { upgrade, ...wsHeaders } = apply(ws);
  assert.equal(upgrade, "websocket");
  assert.deepEqual(apply(http), wsHeaders);
  assert.equal(apply(ws).origin, "https://evil.example");
});

test("launch tokens are captured and redacted at every byte split, including EOF", () => {
  const token = "SuperSecret_123-abc";
  const line = `提示 dsh web: http://localhost:3081/?token=${token}&foo=bar\n`;
  const bytes = Buffer.from(line);
  for (let i = 0; i <= bytes.length; i++) {
    let output = ""; let captured;
    const filter = createLaunchLogFilter({ write: text => { assert.ok(!text.includes(token)); output += text; }, onToken: value => { captured = value; } });
    filter.push(bytes.subarray(0, i)); filter.push(bytes.subarray(i)); filter.end();
    assert.equal(captured, token);
    assert.equal(output, line.replace(token, "[REDACTED]"));
  }
  let output = ""; let captured;
  const filter = createLaunchLogFilter({ write: text => { output += text; }, onToken: value => { captured = value; } });
  for (const byte of bytes.subarray(0, bytes.length - 1)) filter.push(Buffer.from([byte]));
  assert.equal(output, ""); filter.end();
  assert.equal(captured, token); assert.ok(!output.includes(token));
});

test("all token query values in a multi-line chunk are masked", () => {
  let output = "";
  const filter = createLaunchLogFilter({ write: text => { output += text; }, onToken: () => {} });
  filter.push("normal\ndsh web: http://localhost/?token=first&token=second\nother ?TOKEN=third\n");
  filter.end();
  assert.equal(output, "normal\ndsh web: http://localhost/?token=[REDACTED]&token=[REDACTED]\nother ?TOKEN=[REDACTED]\n");
});

test("oversized lines fail closed and normal lines resume", () => {
  let output = "";
  const filter = createLaunchLogFilter({ write: text => { output += text; }, onToken: () => assert.fail("oversized token must not be used"), maxLineLength: 32 });
  filter.push("dsh web: http://localhost/?token="); filter.push("S".repeat(100)); filter.push("\nnormal\n"); filter.end();
  assert.equal(output, "[dsh-relay] oversized stdout line suppressed\nnormal\n");
});
