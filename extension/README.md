# Distil — Prompt Compressor (browser extension)

Compresses what you type into **claude.ai**, **chatgpt.com**, and
**gemini.google.com** *before* it's sent — same meaning, fewer tokens. Unlike
the [API Gateway](../README.md#llm-gateway-drop-in-proxy--the-business-product),
this needs no API key: it sits inside the chat website itself, in your
browser, using your normal logged-in session.

```
you type in the chat box → Distil compresses it → the website sends the compressed text, as if you'd typed that
```

## How it works

Each site has a tiny content script that watches the chat composer. When you
hit Enter or click Send:

1. It stops the send, reads what you typed.
2. Sends it to `https://getdistil.vercel.app/compress` (via the extension's
   background worker, not the page — so the site's own CSP can't block it).
3. Swaps the compressed text into the composer.
4. Lets the site's own Send action proceed, now with the compressed text.

If compression fails for any reason (network error, Distil down, extension
reloading mid-flight), your original message is sent unchanged — it never
gets stuck or dropped. This was verified with an automated test that
deliberately breaks the compression call and confirms the send still goes
through (`tests/send_interception.test.js`).

A small toast in the bottom-right corner shows what happened, e.g.
`Distil compressed 42→19 tokens (-54.8%)`.

## Install (unpacked — not yet on the Chrome Web Store)

1. Open `chrome://extensions` (or `edge://extensions` on Edge).
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this `extension/` folder.
4. Pin the Distil icon (puzzle-piece icon in the toolbar → pin).
5. Open claude.ai, chatgpt.com, or gemini.google.com, type a wordy prompt,
   and hit Enter — watch for the toast.

Click the toolbar icon to open the popup: toggle compression on/off, see your
last compression and running totals, and jump to the live dashboard.

## Metrics

Every compression the extension does is tagged `client: "extension"` and
`site: "claude.ai"` (etc.) and recorded by the same backend the public demo
uses — so it shows up on **https://getdistil.vercel.app** itself, broken out
under "Via Browser Extension" (also available as JSON at `/metrics` →
`by_client.extension`), not just in the popup. The popup also keeps a local
running total ("this browser, all time") since the dashboard's numbers are
global across everyone using Distil, not just you.

## What's verified vs. not

- **claude.ai and gemini.google.com**: selectors (`data-testid="chat-input"` /
  `button[aria-label="Send message"]` for Claude; `.ql-editor` /
  `button[aria-label="Send message"]` for Gemini) were read directly off the
  live sites' real DOM this session — not guessed.
- **chatgpt.com**: I could not reach an anonymous/logged-in session to
  inspect the live DOM this session, so `content/chatgpt.js` uses the
  long-stable public identifiers ChatGPT has kept across redesigns
  (`#prompt-textarea`, `button[data-testid="send-button"]`) with fallback
  selectors, but this path is **not live-verified**. If the extension
  doesn't work on ChatGPT, this is the file to check first — open the
  browser console on chatgpt.com and check whether `document.querySelector('#prompt-textarea')`
  returns something.
- **Send-interception logic** (the trickiest part — stopping the real send,
  compressing async, then re-triggering it exactly once without an infinite
  loop) is covered by `tests/send_interception.test.js`, which simulates a
  host page's real send handler in jsdom and checks: the real handler fires
  exactly once, sees the compressed text, and — critically — still fires
  even when the compression call itself throws.
- **Actually loading and clicking through the extension in a live browser**
  was not done this session (no tool here can drive Chrome's
  `chrome://extensions` "Load unpacked" file picker or your logged-in
  chatgpt.com session) — do the install steps above and try it; the toast
  and popup stats will tell you immediately whether it's working.

## Known caveats

- **DOM fragility.** These sites redesign their chat UI periodically. If a
  selector breaks, the content script's `getComposer`/`getSendButton`
  functions just return `null` and the site behaves normally (no
  interception) — the extension degrades to a no-op rather than breaking
  the chat, but you also won't get compression until the selector's fixed.
- **Compressed text ends up in your chat history**, not your original
  wording — the model (and you, scrolling back later) will see the
  compressed version.
- **Heuristic compression only** (free, instant, no LLM call) — it can read
  slightly terse. How much to compress is decided by Distil, not you — there's
  no ratio knob to tune; if quality ever needs adjusting, that's a backend
  default to revisit, not a per-user setting.
- **Desktop browser only.** Mobile apps (Claude/ChatGPT/Gemini iOS/Android)
  aren't covered — there's no equivalent extension surface there.
- **Rate limits.** `/compress` is anonymous and free, shared with Distil's
  public demo; heavy use could occasionally hit a rate limit, in which case
  compression is skipped and your original text is sent (fail-safe).
- **ToS.** This modifies what's typed into third-party sites client-side in
  your own browser before their own JS sends it — the same category of
  action as many prompt-manager/snippet extensions. It doesn't intercept
  network traffic or spoof requests; it edits the composer's text like a
  very fast paste. Use at your own discretion per each site's terms.

## Files

```
manifest.json          MV3 manifest — permissions, content script matches
background.js          calls /compress (bypasses page CSP), stores last stats
content/common.js      shared send-interception logic (all 3 sites use this)
content/claude.js       claude.ai selectors (live-verified)
content/chatgpt.js      chatgpt.com selectors (NOT live-verified — see above)
content/gemini.js       gemini.google.com selectors (live-verified)
popup/                 toolbar popup: on/off toggle, last + all-time stats, dashboard link
tests/                 jsdom test for the send-interception logic
```

## Run the tests

```bash
cd extension/tests
npm install
node send_interception.test.js
```
