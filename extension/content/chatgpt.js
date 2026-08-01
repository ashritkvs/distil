/**
 * chatgpt.com adapter.
 *
 * NOT live-verified this session (an anonymous "try it" session wasn't
 * reachable to inspect the real composer DOM — see the extension README).
 * Selectors below are the long-stable identifiers ChatGPT has used across
 * multiple redesigns (`#prompt-textarea`, `data-testid="send-button"`), with
 * defensive fallbacks in case they've changed. Test this adapter first if
 * something in the extension isn't working.
 */
function distilGetChatGptComposer() {
  return (
    document.querySelector('#prompt-textarea') ||
    document.querySelector('div[contenteditable="true"][data-id]') ||
    document.querySelector('form div[contenteditable="true"]')
  );
}

function distilGetChatGptSendButton() {
  return (
    document.querySelector('button[data-testid="send-button"]') ||
    document.querySelector('button[aria-label*="Send" i]')
  );
}

distilWireComposer({
  site: "chatgpt.com",
  getComposer: distilGetChatGptComposer,
  getSendButton: distilGetChatGptSendButton,
  getText: (composer) => composer.innerText,
});
