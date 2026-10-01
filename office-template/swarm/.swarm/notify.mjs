#!/usr/bin/env node
// 發一則通知到 Discord 頻道（用公司專屬的 hives/<公司>/.keys/discord-webhook.url，沒有就用共用的 .swarm/keys/shared/）：
//   node .swarm/notify.mjs <公司代號> "訊息內容"
//   echo "較長的訊息" | node .swarm/notify.mjs <公司代號>
// 沒有設定 webhook 時什麼都不做並說明。訊息超過 1900 字會截斷；看起來像金鑰的內容會被遮蔽。
import { readKey, redact } from './keys.mjs';

const [hive, ...rest] = process.argv.slice(2);
if (!hive) {
  console.log('用法：node .swarm/notify.mjs <公司代號> "訊息內容"');
  process.exit(2);
}
let text = rest.join(' ');
if (!text && !process.stdin.isTTY) {
  for await (const chunk of process.stdin) { text += chunk; if (text.length > 20000) break; }
}
text = redact(text.trim());
if (!text) { console.log('✗ 沒有訊息內容'); process.exit(2); }
if (text.length > 1900) text = text.slice(0, 1900) + '…（已截斷）';

let url;
try {
  url = readKey(hive, 'discord');
} catch (error) {
  console.log(`✗ ${error.message}`);
  process.exit(1);
}
if (!url) {
  console.log('ℹ 還沒接 Discord（公司專屬與共用層都沒有 discord-webhook.url），未發送。');
  process.exit(0);
}
try {
  const response = await fetch(url + '?wait=true', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // 不允許 @everyone／@here／提及任何人。
    body: JSON.stringify({ content: text, allowed_mentions: { parse: [] } }),
    signal: AbortSignal.timeout(15000),
  });
  if (response.status === 429) { console.log('✗ Discord 限流中，稍後再試'); process.exit(1); }
  if (!response.ok) { console.log(`✗ Discord 回應 ${response.status}，未發送`); process.exit(1); }
  console.log('✓ 已發送到 Discord');
} catch (error) {
  console.log(redact(`✗ 發送失敗：${error.message}`));
  process.exit(1);
}
