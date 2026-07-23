/**
 * webhook/worker.js
 *
 * LINE Webhook 受信＋読者設定の保存（機能拡張3）。Cloudflare Workers + D1（無料枠）。
 *
 * 受け持つこと:
 *   - POST /webhook : LINE からのイベント受信（署名検証つき）
 *       follow      → users に登録（再フォローは active=1 に戻す）
 *       unfollow    → active=0（以後の配信対象から外れる）
 *       postback    → リッチメニュー/カードのボタン（価格帯設定・気になる登録 等）
 *       message     → 「保有 1234」「保有解除 1234」のテキストコマンド
 *   - GET  /export  : バッチ（GitHub Actions）向けの設定エクスポート（Bearer認証）
 *
 * 返信はすべて reply API（応答メッセージ）で行う。応答メッセージは
 * 従量課金の対象外なので、設定操作がメッセージ予算を消費しない。
 *
 * 必要な設定（wrangler.toml / ダッシュボード）:
 *   - D1 バインディング: DB
 *   - 環境変数: LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, EXPORT_TOKEN
 */

const DISCLAIMER = "\n※本サービスは投資助言ではありません。表示される価格・期間は機械的に算出した参考値です。";

// 友だち追加時のウェルカムメッセージ（改善4）。reply APIなので通数は消費しない。
// 使い方・設定方法・免責をここで案内し、パーソナライズ設定への導線を作る。
const WELCOME_TEXT =
  "友だち追加ありがとうございます！\n" +
  "毎朝（平日）、東証銘柄を機械的な条件でスクリーニングした上位銘柄をお届けします。\n" +
  "\n" +
  "【できること（下のメニューから設定）】\n" +
  "・価格帯フィルタ：単元購入価格の上限（例：10万円以下）で表示銘柄を絞れます\n" +
  "・気になる登録：配信カードの「⭐気になる」を押すと、その銘柄の上値メド到達・" +
  "参考下値ライン割れ・目安保有期間の満了を個別にお知らせします\n" +
  "・保有銘柄の登録：「保有 1234」のように証券コードを送ると登録できます" +
  "（解除は「保有解除 1234」）\n" +
  "\n" +
  "【継続開示】\n" +
  "掲載銘柄のその後（1日/3営業日/1週間/1ヶ月後）と、日経平均と比較した累積成績を、" +
  "勝った月も負けた月も同じ基準で毎月開示します。\n" +
  "\n" +
  "【重要なお知らせ】\n" +
  "本サービスは投資助言ではなく、公開データに基づく機械的なスクリーニング結果の" +
  "提供です。記載の価格・期間はすべて機械的に算出した参考値であり、将来の成果を" +
  "保証しません。投資は必ずご自身の判断と責任で行ってください。";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/webhook") {
      return handleWebhook(request, env);
    }
    if (request.method === "GET" && url.pathname === "/export") {
      return handleExport(request, env);
    }
    return new Response("ok", { status: 200 });
  },
};

// ====== /export（バッチ向け・Bearer認証） ======
async function handleExport(request, env) {
  const auth = request.headers.get("Authorization") || "";
  if (auth !== `Bearer ${env.EXPORT_TOKEN}`) {
    return new Response("unauthorized", { status: 401 });
  }
  const users = await env.DB.prepare(
    "SELECT user_id, price_cap, active FROM users").all();
  const watch = await env.DB.prepare(
    "SELECT user_id, code, kind FROM watch_items").all();
  return Response.json({
    users: users.results || [],
    watch_items: watch.results || [],
  });
}

// ====== /webhook（LINE 署名検証 → イベント処理） ======
async function handleWebhook(request, env) {
  const body = await request.text();
  const signature = request.headers.get("x-line-signature") || "";
  if (!(await verifySignature(body, signature, env.LINE_CHANNEL_SECRET))) {
    return new Response("bad signature", { status: 403 });
  }
  let events = [];
  try {
    events = JSON.parse(body).events || [];
  } catch (_e) {
    return new Response("bad request", { status: 400 });
  }
  for (const ev of events) {
    try {
      await handleEvent(ev, env);
    } catch (e) {
      // 1イベントの失敗で全体を落とさない（LINE側の再送を避けるため200を返す）
      console.log("event error:", e);
    }
  }
  return new Response("ok", { status: 200 });
}

async function verifySignature(body, signature, secret) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  return expected === signature;
}

async function handleEvent(ev, env) {
  const userId = ev?.source?.userId;
  if (!userId) return;
  const now = new Date().toISOString();

  if (ev.type === "follow") {
    await env.DB.prepare(
      `INSERT INTO users (user_id, active, created_at, updated_at)
       VALUES (?, 1, ?, ?)
       ON CONFLICT(user_id) DO UPDATE SET active=1, updated_at=excluded.updated_at`
    ).bind(userId, now, now).run();
    // ウェルカム自動応答（使い方・設定方法・免責）。reply なので通数消費なし。
    await reply(ev.replyToken, env, WELCOME_TEXT);
    return;
  }
  if (ev.type === "unfollow") {
    await env.DB.prepare(
      "UPDATE users SET active=0, updated_at=? WHERE user_id=?"
    ).bind(now, userId).run();
    return;
  }
  if (ev.type === "postback") {
    const params = new URLSearchParams(ev.postback?.data || "");
    await handleAction(userId, params, ev.replyToken, env, now);
    return;
  }
  if (ev.type === "message" && ev.message?.type === "text") {
    // テキストコマンド: 「保有 1234」「保有解除 1234」（証券コード4〜5桁）
    const text = (ev.message.text || "").trim();
    let m = text.match(/^保有[  ]*([0-9]{4}[0-9A-Z]?)$/);
    if (m) {
      await addWatch(userId, m[1], "holding", env, now);
      await reply(ev.replyToken, env,
        `保有銘柄（${m[1]}）を登録しました。関連テーマに動きがあった際にお知らせします。` +
        `解除は「保有解除 ${m[1]}」と送信してください。${DISCLAIMER}`);
      return;
    }
    m = text.match(/^保有解除[  ]*([0-9]{4}[0-9A-Z]?)$/);
    if (m) {
      await env.DB.prepare(
        "DELETE FROM watch_items WHERE user_id=? AND code=? AND kind='holding'"
      ).bind(userId, m[1]).run();
      await reply(ev.replyToken, env, `保有銘柄（${m[1]}）の登録を解除しました。`);
      return;
    }
  }
}

async function handleAction(userId, params, replyToken, env, now) {
  const action = params.get("action");

  if (action === "cap") {
    // 価格帯フィルタの設定（単元購入価格の上限・円）。value=0 は解除。
    const value = Number(params.get("value") || 0);
    const cap = value > 0 ? value : null;
    await env.DB.prepare(
      `INSERT INTO users (user_id, price_cap, active, created_at, updated_at)
       VALUES (?, ?, 1, ?, ?)
       ON CONFLICT(user_id) DO UPDATE SET price_cap=excluded.price_cap,
         active=1, updated_at=excluded.updated_at`
    ).bind(userId, cap, now, now).run();
    const label = cap ? `${Math.round(cap / 10000)}万円以下` : "設定なし（全銘柄）";
    await reply(replyToken, env,
      `価格帯フィルタを「単元購入価格 ${label}」に設定しました。` +
      `明日の配信から反映されます。${DISCLAIMER}`);
    return;
  }

  if (action === "interest") {
    // 配信カードの「気になる」ボタン
    const code = (params.get("code") || "").trim();
    const name = (params.get("name") || "").trim();
    if (!code) return;
    await addWatch(userId, code, "interest", env, now);
    await reply(replyToken, env,
      `${name || code} を「気になる」に登録しました。` +
      `上値メド到達・参考下値ライン割れ・目安保有期間の満了時に個別でお知らせします。${DISCLAIMER}`);
    return;
  }

  if (action === "settings") {
    // 現在の設定を返す（確認用）
    const u = await env.DB.prepare(
      "SELECT price_cap FROM users WHERE user_id=? AND active=1").bind(userId).first();
    const items = await env.DB.prepare(
      "SELECT code, kind FROM watch_items WHERE user_id=? ORDER BY created_at DESC LIMIT 20"
    ).bind(userId).all();
    const cap = u?.price_cap ? `${Math.round(u.price_cap / 10000)}万円以下` : "設定なし（全銘柄）";
    const interest = (items.results || []).filter(r => r.kind === "interest").map(r => r.code);
    const holding = (items.results || []).filter(r => r.kind === "holding").map(r => r.code);
    await reply(replyToken, env,
      `【現在の設定】\n価格帯: 単元購入価格 ${cap}\n` +
      `気になる: ${interest.length ? interest.join(", ") : "なし"}\n` +
      `保有: ${holding.length ? holding.join(", ") : "なし"}\n` +
      `保有銘柄の登録は「保有 1234」のように証券コードを送信してください。`);
    return;
  }
}

async function addWatch(userId, code, kind, env, now) {
  await env.DB.prepare(
    `INSERT INTO users (user_id, active, created_at, updated_at) VALUES (?, 1, ?, ?)
     ON CONFLICT(user_id) DO UPDATE SET active=1, updated_at=excluded.updated_at`
  ).bind(userId, now, now).run();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO watch_items (user_id, code, kind, created_at)
     VALUES (?, ?, ?, ?)`
  ).bind(userId, code, kind, now).run();
}

async function reply(replyToken, env, text) {
  if (!replyToken) return;
  await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
    },
    body: JSON.stringify({
      replyToken,
      messages: [{ type: "text", text: text.slice(0, 4900) }],
    }),
  });
}
