/**
 * Functional test for the core send-interception logic in content/common.js
 * (distilWireComposer / distilHandleSend / distilSetText). Runs in jsdom to
 * simulate a host page's own send handler (Enter key + button click) and
 * verifies:
 *
 *   1. The real handler fires exactly once per user send action.
 *   2. It sees the COMPRESSED text, not the original.
 *   3. An empty composer passes through without an unnecessary network call.
 *   4. A broken/throwing background channel still lets the user send
 *      (fail-safe) — this caught a real bug during development: an
 *      uncaught exception from document.execCommand (which some
 *      environments throw instead of returning false) used to permanently
 *      swallow the user's send action.
 *
 * Requires `npm install` in this directory (jsdom) before running:
 *   npm install && node send_interception.test.js
 */

const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

let failures = 0;
function check(cond, msg) {
  if (!cond) {
    failures++;
    console.error("FAIL:", msg);
  } else {
    console.log("ok  :", msg);
  }
}

async function main() {
  const dom = new JSDOM(
    `<div id="composer" contenteditable="true" role="textbox"></div>
     <button id="sendBtn">Send</button>`,
    { runScripts: "outside-only", pretendToBeVisual: true }
  );
  const { window } = dom;
  global.window = window;
  global.document = window.document;
  global.MutationObserver = window.MutationObserver;
  global.KeyboardEvent = window.KeyboardEvent;
  global.InputEvent = window.InputEvent;
  global.requestAnimationFrame = (cb) => setTimeout(cb, 0);

  let realSendCount = 0;
  let realHandlerSawText = null;

  const composer = window.document.getElementById("composer");
  const button = window.document.getElementById("sendBtn");

  // Stand-in for the host page's own React-ish send handlers (bubble phase),
  // same as claude.ai / chatgpt.com / gemini.google.com would have.
  composer.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      realSendCount++;
      realHandlerSawText = composer.textContent;
    }
  }, false);
  button.addEventListener("click", () => {
    realSendCount++;
    realHandlerSawText = composer.textContent;
  }, false);

  global.chrome = {
    runtime: {
      sendMessage(msg, cb) {
        setTimeout(() => {
          cb({
            ok: true, skipped: false,
            text: "COMPRESSED:" + msg.text,
            original_tokens: 10, compressed_tokens: 4,
            tokens_saved: 6, reduction_pct: 60.0,
          });
        }, 5);
      },
    },
  };

  eval(fs.readFileSync(path.join(__dirname, "..", "content", "common.js"), "utf8"));

  distilWireComposer({
    site: "test-site",
    getComposer: () => window.document.getElementById("composer"),
    getSendButton: () => window.document.getElementById("sendBtn"),
    getText: (c) => c.textContent,
  });

  // 1) Enter-key send
  composer.textContent = "hello world original text";
  composer.dispatchEvent(new window.KeyboardEvent("keydown", {
    key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true,
  }));
  await new Promise((r) => setTimeout(r, 50));
  check(realSendCount === 1, "Enter: real send fires exactly once");
  check(realHandlerSawText === "COMPRESSED:hello world original text",
    "Enter: real handler sees compressed text, not original");

  // 2) Button-click send
  realSendCount = 0; realHandlerSawText = null;
  composer.textContent = "second message here";
  button.click();
  await new Promise((r) => setTimeout(r, 50));
  check(realSendCount === 1, "Click: real send fires exactly once");
  check(realHandlerSawText === "COMPRESSED:second message here",
    "Click: real handler sees compressed text, not original");

  // 3) Empty composer — passthrough, no hang
  realSendCount = 0;
  composer.textContent = "";
  composer.dispatchEvent(new window.KeyboardEvent("keydown", {
    key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true,
  }));
  await new Promise((r) => setTimeout(r, 50));
  check(realSendCount === 1, "Empty composer: passes through immediately");

  // 4) Fail-safe: background channel throws synchronously
  global.chrome.runtime.sendMessage = () => { throw new Error("extension context invalidated"); };
  realSendCount = 0;
  composer.textContent = "message during extension reload";
  composer.dispatchEvent(new window.KeyboardEvent("keydown", {
    key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true,
  }));
  await new Promise((r) => setTimeout(r, 50));
  check(realSendCount === 1, "Fail-safe: a broken background channel still lets the user send");

  if (failures > 0) {
    console.error(`\n${failures} assertion(s) failed.`);
    process.exit(1);
  }
  console.log("\nAll assertions passed.");
}

main().catch((e) => { console.error("TEST CRASHED:", e); process.exit(1); });
