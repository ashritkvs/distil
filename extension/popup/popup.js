const DEFAULT_SETTINGS = { enabled: true, ratio: 0.5 };

const enabledEl = document.getElementById("enabled");
const ratioEl = document.getElementById("ratio");
const ratioValueEl = document.getElementById("ratioValue");
const statsEl = document.getElementById("stats");
const statsSiteEl = document.getElementById("statsSite");
const statsTokensEl = document.getElementById("statsTokens");

function renderRatio(ratio) {
  ratioValueEl.textContent = `${Math.round(ratio * 100)}%`;
}

async function load() {
  const settings = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  enabledEl.checked = settings.enabled;
  ratioEl.value = settings.ratio;
  renderRatio(settings.ratio);

  const { lastStats } = await chrome.storage.local.get("lastStats");
  if (lastStats && Date.now() - lastStats.ts < 1000 * 60 * 60 * 6) {
    statsEl.hidden = false;
    statsSiteEl.textContent = lastStats.site;
    statsTokensEl.textContent =
      `${lastStats.original_tokens} → ${lastStats.compressed_tokens} tokens ` +
      `(-${lastStats.reduction_pct}%)`;
  }
}

enabledEl.addEventListener("change", () => {
  chrome.storage.sync.set({ enabled: enabledEl.checked });
});

ratioEl.addEventListener("input", () => {
  renderRatio(Number(ratioEl.value));
});

ratioEl.addEventListener("change", () => {
  chrome.storage.sync.set({ ratio: Number(ratioEl.value) });
});

load();
