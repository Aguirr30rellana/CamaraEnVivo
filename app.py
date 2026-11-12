import os
import webview

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "webview_data")
os.makedirs(STORAGE_DIR, exist_ok=True)

TARGET_URL = "https://protect-us.ismartlife.me"

WATCHDOG_JS = """
(() => {
    if (window.__cameraWatchdogActive) return;
    window.__cameraWatchdogActive = true;

    let lastTime = -1;
    let stallCount = 0;
    let lastReload = 0;

    const badge = document.createElement('div');
    badge.style.cssText = `
        position: fixed; bottom: 12px; left: 12px; z-index: 999999;
        background: rgba(15, 23, 42, 0.85); color: #4ade80;
        font-family: Segoe UI, sans-serif; font-size: 12px; font-weight: 600;
        padding: 6px 12px; border-radius: 20px; border: 1px solid rgba(74, 222, 128, 0.3);
        pointer-events: none; backdrop-filter: blur(4px);
    `;
    badge.textContent = '● Monitor 24/7 Activo';
    document.body.appendChild(badge);

    function updateBadge(text, color) {
        badge.textContent = text;
        badge.style.color = color;
        badge.style.borderColor = color;
    }

    function safeReload(reason) {
        const now = Date.now();
        if (now - lastReload < 15000) return;
        lastReload = now;
        updateBadge('↻ Reconectando (' + reason + ')...', '#facc15');
        setTimeout(() => window.location.reload(), 1000);
    }

    setInterval(() => {
        const bodyText = document.body.innerText || '';

        if (window.location.href.includes('login') || bodyText.includes('Scan QR') || bodyText.includes('扫码登录')) {
            updateBadge('● Esperando inicio de sesión QR', '#38bdf8');
            return;
        }

        const langBtn = Array.from(document.querySelectorAll('span, div, button'))
            .find(el => el.textContent.trim() === '简体中文');
        if (langBtn) {
            langBtn.click();
            setTimeout(() => {
                const esOrEn = Array.from(document.querySelectorAll('li, span, div'))
                    .find(el => ['Español', 'English'].includes(el.textContent.trim()));
                if (esOrEn) esOrEn.click();
            }, 400);
        }

        const errorKeywords = [
            'Play failed', 'Connection failed', 'Timeout', 'Network error',
            'Retry', 'Refresh', 'Reintentar', '播放失败', '连接超时', '重新加载', '重试'
        ];
        const hasErrorText = errorKeywords.some(kw => bodyText.includes(kw));

        if (hasErrorText) {
            const retryBtn = Array.from(document.querySelectorAll('button, a, div[role="button"], span'))
                .find(el => ['Retry', 'Refresh', 'Reintentar', '重试', '重新加载'].includes(el.textContent.trim()));
            
            if (retryBtn) {
                updateBadge('↻ Reintentando flujo...', '#facc15');
                retryBtn.click();
                stallCount = 0;
                return;
            } else {
                safeReload('Error de red');
                return;
            }
        }

        const video = document.querySelector('video');
        if (video) {
            if (video.currentTime > 0 && video.currentTime !== lastTime && !video.paused) {
                lastTime = video.currentTime;
                stallCount = 0;
                updateBadge('● En vivo (24/7)', '#4ade80');
            } else {
                stallCount++;
                updateBadge(`⚠ Verificando señal (${stallCount * 2}s)...`, '#fb923c');
                
                if (video.paused && stallCount === 2) {
                    video.play().catch(() => {});
                }
                if (stallCount >= 5) {
                    safeReload('Video congelado');
                }
            }
        } else {
            const cameraCard = Array.from(document.querySelectorAll('div, span, p'))
                .find(el => el.textContent && el.textContent.includes('CCTV-638'));
            if (cameraCard) {
                updateBadge('↻ Abriendo CCTV-638...', '#38bdf8');
                cameraCard.click();
            }
        }
    }, 2000);
})();
"""

def on_loaded(window):
    print("Portal cargado correctamente. Inyectando Watchdog 24/7...")
    try:
        window.evaluate_js(WATCHDOG_JS)
    except Exception as e:
        print(f"Error inyectando watchdog: {e}")

if __name__ == "__main__":
    print("Iniciando ventana del monitor...")
    try:
        window = webview.create_window(
            title="Monitor Steren CCTV-638 (24/7)",
            url=TARGET_URL,
            width=1280,
            height=720,
            resizable=True
        )
        window.events.loaded += on_loaded

        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=STORAGE_DIR
        )
    except Exception as e:
        print(f"Error al abrir la ventana: {e}")