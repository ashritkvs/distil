const DEFAULT_SETTINGS = { enabled: true };

const enabledEl = document.getElementById("enabled");
const statsEl = document.getElementById("stats");
const statsSiteEl = document.getElementById("statsSite");
const statsTokensEl = document.getElementById("statsTokens");
const totalsRowEl = document.getElementById("totalsRow");

async function load() {
  const settings = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  enabledEl.checked = settings.enabled;

  const { lastStats, totals } = await chrome.storage.local.get({
    lastStats: null,
    totals: { requests: 0, tokensSaved: 0 },
  });

  if (lastStats && Date.now() - lastStats.ts < 1000 * 60 * 60 * 6) {
    statsEl.hidden = false;
    statsSiteEl.textContent = lastStats.site;
    statsTokensEl.textContent =
      `${lastStats.original_tokens} → ${lastStats.compressed_tokens} tokens ` +
      `(-${lastStats.reduction_pct}%)`;
  }

  totalsRowEl.textContent = `${totals.requests} messages · ${totals.tokensSaved} tokens saved`;
}

enabledEl.addEventListener("change", () => {
  chrome.storage.sync.set({ enabled: enabledEl.checked });
});

load();
