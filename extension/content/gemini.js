/**
 * gemini.google.com adapter. Selectors verified live 2026-07-31 against the
 * real gemini.google.com composer (Quill-based):
 *   input:  div.ql-editor[contenteditable="true"][aria-label="Enter a prompt for Gemini"]
 *   send:   button[aria-label="Send message"]
 */
distilWireComposer({
  site: "gemini.google.com",
  getComposer: () =>
    document.querySelector('div.ql-editor[contenteditable="true"]') ||
    document.querySelector('[aria-label="Enter a prompt for Gemini"]'),
  getSendButton: () =>
    document.querySelector('button[aria-label="Send message"]'),
  getText: (composer) => composer.innerText,
});
