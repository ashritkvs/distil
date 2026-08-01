/**
 * Shared helpers used by every per-site content script (claude.js, chatgpt.js,
 * gemini.js). Loaded before the site-specific script in manifest.json, so
 * these are plain globals in the isolated content-script world — no imports.
 */

const DISTIL_STATE = { bypassNextSend: false };

/** Ask the background worker to compress `text`. Never throws — on any
 * failure (network, extension reload, disabled) it resolves with
 * `skipped: true` and the original text, so callers can always fall back. */
function distilCompress(text, site) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: "DISTIL_COMPRESS", text, site }, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          resolve({ ok: false, skipped: true, text });
          return;
        }
        resolve(resp);
      });
    } catch (e) {
      resolve({ ok: false, skipped: true, text });
    }
  });
}

/** Replace the text of a contenteditable composer using execCommand, so the
 * host page's own editor framework (ProseMirror/Quill/etc.) sees real
 * input events and stays in sync — directly setting textContent leaves
 * these editors' internal state stale or broken. execCommand is deprecated
 * and some environments throw rather than returning false, so this never
 * lets that escape — worst case it falls back to a plain textContent swap. */
function distilSetText(el, text) {
  el.focus();
  let inserted = false;
  try {
    const selectedAll = document.execCommand("selectAll", false, null);
    inserted = !!selectedAll && document.execCommand("insertText", false, text);
  } catch (e) {
    inserted = false;
  }
  if (!inserted) {
    el.textContent = text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true }));
  }
}

function distilToast(message) {
  let host = document.getElementById("__distil_toast_host");
  if (!host) {
    host = document.createElement("div");
    host.id = "__distil_toast_host";
    Object.assign(host.style, {
      position: "fixed", bottom: "24px", right: "24px", zIndex: "2147483647",
      display: "flex", flexDirection: "column", gap: "8px", pointerEvents: "none",
    });
    document.documentElement.appendChild(host);
  }
  const toast = document.createElement("div");
  toast.textContent = message;
  Object.assign(toast.style, {
    background: "#1e293b", color: "#f8fafc", padding: "8px 14px",
    borderRadius: "8px", fontSize: "13px",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    boxShadow: "0 4px 12px rgba(0,0,0,0.25)", opacity: "0",
    transition: "opacity 200ms ease", maxWidth: "280px",
  });
  host.appendChild(toast);
  requestAnimationFrame(() => { toast.style.opacity = "1"; });
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 250);
  }, 2200);
}

/** Shared send-interception flow: compress the composer's current text (if
 * any), swap it in, show a toast if it actually changed, then call
 * `doRealSend` to let the host page's own send action proceed. Fail-safe by
 * construction — `distilCompress` never throws and always resolves. */
async function distilHandleSend(getText, setText, doRealSend, site) {
  let original = "";
  try {
    original = getText() || "";
  } catch (e) {
    // fall through — doRealSend still fires below
  }
  if (!original.trim()) {
    doRealSend();
    return;
  }
  // `doRealSend` MUST fire exactly once no matter what happens above it —
  // we already preventDefault()-ed the user's real send, so any unhandled
  // exception here would silently break their ability to send at all.
  try {
    const result = await distilCompress(original, site);
    if (!result.skipped && result.text && result.text !== original) {
      setText(result.text);
      if (typeof result.tokens_saved === "number" && result.tokens_saved > 0) {
        distilToast(`Distil compressed ${result.original_tokens}→${result.compressed_tokens} tokens (-${result.reduction_pct}%)`);
      }
    } else if (result.reason === "error") {
      distilToast("Distil: compression failed, sent as-is");
    }
  } catch (e) {
    // Fail-safe: swallow and send the original, untouched text.
  } finally {
    doRealSend();
  }
}

/**
 * Wires send-interception onto a site's composer + send button. Handles the
 * two ways a message gets sent (Enter key, button click), the async
 * compress-before-send flow, and re-issuing the real send afterward without
 * re-triggering our own interceptor (the `bypassNextSend` flag).
 *
 * The composer/button elements get remounted whenever a site's SPA router
 * navigates (new chat, page nav), so a MutationObserver re-scans and
 * re-wires as needed; a `__distilWired` marker keeps wiring idempotent.
 *
 * config: { site, getComposer(): Element|null, getSendButton(): Element|null,
 *           getText(composer): string }
 */
function distilWireComposer(config) {
  const { site, getComposer, getSendButton, getText } = config;

  function wireComposer(composer) {
    if (composer.__distilWired) return;
    composer.__distilWired = true;
    composer.addEventListener("keydown", async (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      if (DISTIL_STATE.bypassNextSend) { DISTIL_STATE.bypassNextSend = false; return; }
      e.preventDefault();
      e.stopImmediatePropagation();
      await distilHandleSend(
        () => getText(composer),
        (t) => distilSetText(composer, t),
        () => {
          DISTIL_STATE.bypassNextSend = true;
          composer.dispatchEvent(new KeyboardEvent("keydown", {
            key: "Enter", code: "Enter", keyCode: 13, which: 13,
            bubbles: true, cancelable: true,
          }));
        },
        site
      );
    }, true);
  }

  function wireButton(button) {
    if (button.__distilWired) return;
    button.__distilWired = true;
    button.addEventListener("click", async (e) => {
      if (DISTIL_STATE.bypassNextSend) { DISTIL_STATE.bypassNextSend = false; return; }
      const composer = getComposer();
      if (!composer) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      await distilHandleSend(
        () => getText(composer),
        (t) => distilSetText(composer, t),
        () => {
          DISTIL_STATE.bypassNextSend = true;
          button.click();
        },
        site
      );
    }, true);
  }

  function scan() {
    const composer = getComposer();
    if (composer) wireComposer(composer);
    const button = getSendButton();
    if (button) wireButton(button);
  }

  scan();
  new MutationObserver(scan).observe(document.documentElement, {
    childList: true, subtree: true,
  });
}
