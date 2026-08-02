/**
 * Distil extension background service worker.
 *
 * Content scripts never call the Distil API directly — they message this
 * worker, which does the fetch. Two reasons: (1) a background worker isn't
 * subject to the host page's Content-Security-Policy, so the request can't
 * be silently blocked by claude.ai/chatgpt.com/gemini.google.com's own CSP;
 * (2) it's one place to read settings and record the last-compression stats
 * shown in the popup.
 *
 * Every call tags `client: "extension"` and `site` on the request, so this
 * usage shows up broken out (not just blended into the totals) on the live
 * Distil dashboard at https://getdistil.vercel.app — see the "Via Browser
 * Extension" tiles there, or GET /metrics -> `by_client.extension`.
 */

const DISTIL_ENDPOINT = "https://getdistil.vercel.app/compress";
const DEFAULT_SETTINGS = { enabled: true };
// How much to compress is Distil's own call, not a user knob (see popup) —
// this matches the backend's own default target ratio.
const COMPRESSION_RATIO = 0.5;

async function getSettings() {
  return chrome.storage.sync.get(DEFAULT_SETTINGS);
}

async function recordLocalTotals(tokensSaved) {
  const { totals } = await chrome.storage.local.get({
    totals: { requests: 0, tokensSaved: 0 },
  });
  totals.requests += 1;
  totals.tokensSaved += tokensSaved;
  await chrome.storage.local.set({ totals });
}

async function compress(text, site) {
  const settings = await getSettings();

  if (!settings.enabled) {
    return { ok: true, skipped: true, reason: "disabled", text };
  }
  if (!text || !text.trim()) {
    return { ok: true, skipped: true, reason: "empty", text };
  }

  try {
    const res = await fetch(DISTIL_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: text,
        target_ratio: COMPRESSION_RATIO,
        client: "extension",
        site,
      }),
    });
    if (!res.ok) throw new Error(`Distil HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    const compressedText = (data.compressed_text || "").trim();
    if (!compressedText) {
      // Never ship an empty prompt because compression over-trimmed it.
      return { ok: true, skipped: true, reason: "empty_result", text };
    }

    const result = {
      ok: true,
      skipped: false,
      text: compressedText,
      original_tokens: data.original_tokens,
      compressed_tokens: data.compressed_tokens,
      tokens_saved: data.tokens_saved,
      reduction_pct: data.reduction_pct,
      site,
      ts: Date.now(),
    };
    await chrome.storage.local.set({ lastStats: result });
    await recordLocalTotals(data.tokens_saved || 0);
    return result;
  } catch (err) {
    // Fail-safe: a Distil error must never block the user's message.
    return { ok: false, skipped: true, reason: "error", error: String(err), text };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "DISTIL_COMPRESS") return false;
  compress(msg.text, msg.site).then(sendResponse);
  return true; // keep the message channel open for the async response
});
