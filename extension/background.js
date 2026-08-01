/**
 * Distil extension background service worker.
 *
 * Content scripts never call the Distil API directly — they message this
 * worker, which does the fetch. Two reasons: (1) a background worker isn't
 * subject to the host page's Content-Security-Policy, so the request can't
 * be silently blocked by claude.ai/chatgpt.com/gemini.google.com's own CSP;
 * (2) it's one place to read settings and record the last-compression stats
 * shown in the popup.
 */

const DISTIL_ENDPOINT = "https://getdistil.vercel.app/compress";
const DEFAULT_SETTINGS = { enabled: true, ratio: 0.5 };

async function getSettings() {
  return chrome.storage.sync.get(DEFAULT_SETTINGS);
}

async function compress(text, site) {
  const settings = await getSettings();

  if (!settings.enabled) {
    return { ok: true, skipped: true, reason: "disabled", text };
  }
  if (!text || !text.trim()) {
    return { ok: true, skipped: true, reason: "empty", text };
  }

  let ratio = Number(settings.ratio);
  if (!Number.isFinite(ratio) || ratio < 0.05 || ratio > 1) ratio = 0.5;

  try {
    const res = await fetch(DISTIL_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text, target_ratio: ratio }),
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
