/**
 * Inject the Telegram Mini App fullscreen bootstrap into the saved dashboard.
 *
 * The script loads the official Telegram Web App SDK; then, only when the page
 * is actually opened inside Telegram as a Mini App, it marks the app ready,
 * expands it, and requests fullscreen (Bot API 8.0+, supported on mobile and
 * Telegram Desktop). In a plain browser the script is inert.
 *
 * The injection is idempotent: re-running it on an already-injected document
 * is a no-op, so re-uploads never stack duplicate `<script>` blocks.
 */

const MARKER = "fom-dashboard-worker:telegram-fullscreen";
const SDK_URL = "telegram.org/js/telegram-web-app.js";

const SNIPPET = `
<!-- ${MARKER} : added automatically so the dashboard opens fullscreen in Telegram -->
<script src="https://${SDK_URL}"></script>
<script>
(function () {
  function initTelegramFullscreen() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return;                       // opened in a normal browser
    try { tg.ready(); } catch (e) {}
    try { tg.expand(); } catch (e) {}
    try {
      if (tg.isVersionAtLeast && tg.isVersionAtLeast('8.0') &&
          typeof tg.requestFullscreen === 'function') {
        tg.requestFullscreen();            // Bot API 8.0+ (mobile & desktop)
      }
    } catch (e) {}
    try { if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (e) {}
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTelegramFullscreen);
  } else {
    initTelegramFullscreen();
  }
})();
</script>
`;

export function injectFullscreen(html: string): string {
  if (html.includes(MARKER) || html.includes(SDK_URL)) {
    return html; // already present
  }
  const lowered = html.toLowerCase();
  let insertAt = lowered.lastIndexOf("</body>");
  if (insertAt === -1) insertAt = lowered.lastIndexOf("</html>");
  if (insertAt === -1) return html + SNIPPET;
  return html.slice(0, insertAt) + SNIPPET + html.slice(insertAt);
}
