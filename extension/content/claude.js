/**
 * claude.ai adapter. Selectors verified live 2026-07-31 against the real
 * claude.ai composer (Tiptap/ProseMirror-based):
 *   input:  div[contenteditable="true"][data-testid="chat-input"]
 *   send:   button[aria-label="Send message"]
 */
distilWireComposer({
  site: "claude.ai",
  getComposer: () =>
    document.querySelector('div[contenteditable="true"][data-testid="chat-input"]'),
  getSendButton: () =>
    document.querySelector('button[aria-label="Send message"]'),
  getText: (composer) => composer.innerText,
});
