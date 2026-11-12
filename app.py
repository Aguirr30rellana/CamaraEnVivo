import base64
import datetime
import hashlib
import os
import re
import threading
import time
import urllib.request
import cv2
import numpy as np
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
    _cfg = json.load(f)

IP = _cfg["ip"]
USER = _cfg["user"]
PASSWORD = _cfg["password"]
# Opcionales en config.json:  "transport": "tcp" | "udp"   "hw": true | false
TRANSPORT = _cfg.get("transport", "udp")
USAR_HW = _cfg.get("hw", False)

# Mismas opciones de baja latencia de tu version original.
if TRANSPORT == "tcp":
    # TCP no pierde paquetes: sin discardcorrupt ni cola de reorden
    _opts = (
        "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|"
        "probesize;262144|analyzeduration;200000|stimeout;1500000"
    )
else:
    _opts = (
        "rtsp_transport;udp|fflags;nobuffer+discardcorrupt|flags;low_delay|"
        "avioflags;direct|max_delay;0|reorder_queue_size;0|"
        "probesize;262144|analyzeduration;200000|stimeout;1500000"
    )
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _opts

# Si no llega ningun cuadro durante estos segundos, se reconecta solo
# (la reconexion fuerza un fotograma clave nuevo y descongela la imagen)
SEGUNDOS_CONGELADO = 1.2


def abrir_captura_rapida(url):
    cap = None
    if USAR_HW:
        # La aceleracion por hardware debe pedirse AL ABRIR, no despues
        try:
            cap = cv2.VideoCapture(
                url, cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY],
            )
        except Exception:
            cap = None
    if cap is None:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def obtener_ruta_hd():
    urls = []
    for puerto in [8000, 8080, 8899, 80]:
        for ruta_servicio in ["/onvif/media_service", "/onvif/device_service"]:
            url_media = f"http://{IP}:{puerto}{ruta_servicio}"
            try:
                def soap_call(body):
                    nonce = os.urandom(16)
                    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    digest = base64.b64encode(
                        hashlib.sha1(nonce + created.encode() + PASSWORD.encode()).digest()
                    ).decode()
                    nonce_b64 = base64.b64encode(nonce).decode()
                    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
                    <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
                      <s:Header>
                        <Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
                          <UsernameToken>
                            <Username>{USER}</Username>
                            <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
                            <Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</Nonce>
                            <Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>
                          </UsernameToken>
                        </Security>
                      </s:Header>
                      <s:Body>{body}</s:Body>
                    </s:Envelope>""".encode("utf-8")
                    req = urllib.request.Request(
                        url_media,
                        data=envelope,
                        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                    )
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        return resp.read().decode("utf-8", errors="ignore")

                res_profiles = soap_call('<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>')
                tokens = re.findall(r'<[^:>]*:?Profiles[^>]*token="([^"]+)"', res_profiles)
                if not tokens:
                    tokens = re.findall(r'token="([^"]+)"', res_profiles)

                for token in dict.fromkeys(tokens):
                    res_uri = soap_call(f"""
                        <GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl">
                          <StreamSetup>
                            <Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>
                            <Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>
                          </StreamSetup>
                          <ProfileToken>{token}</ProfileToken>
                        </GetStreamUri>
                    """)
                    match_uri = re.search(r"<[^:>]*:?Uri>([^<]+)</[^:>]*:?Uri>", res_uri)
                    if match_uri:
                        rtsp = match_uri.group(1).replace("&amp;", "&")
                        if "@" not in rtsp:
                            rtsp = rtsp.replace("rtsp://", f"rtsp://{USER}:{PASSWORD}@", 1)
                        if rtsp not in urls:
                            urls.append(rtsp)
                if urls:
                    break
            except Exception:
                continue
        if urls:
            break

    if not urls:
        urls = [
            f"rtsp://{USER}:{PASSWORD}@{IP}:554/live/ch00_0",
            f"rtsp://{USER}:{PASSWORD}@{IP}:554/11",
            f"rtsp://{USER}:{PASSWORD}@{IP}:554/onvif1",
        ]

    mejor_url = urls[0]
    mejor_pixeles = 0
    for url in urls:
        cap = abrir_captura_rapida(url)
        ret, frame = cap.read()
        if ret and frame is not None:
            h, w = frame.shape[:2]
            pixeles = w * h
            if pixeles > mejor_pixeles:
                mejor_pixeles = pixeles
                mejor_url = url
                fps = cap.get(cv2.CAP_PROP_FPS)
                print(f"Flujo HD detectado: {w}x{h} @ {fps:.1f} FPS")
        cap.release()
    return mejor_url


class Monitor247:
    """
    Hilo de captura que SIEMPRE decodifica (grab) para vaciar el flujo y no
    acumular retraso, pero solo convierte a BGR (retrieve, lo caro) cuando la
    ventana ya consumio el cuadro anterior. Asi se muestra siempre el cuadro
    mas reciente y la CPU no se satura con movimiento rapido.
    """

    def __init__(self, url):
        self.url = url
        self.cap = None
        self.frame = None
        self.frame_id = 0
        self.conectado = False
        self.corriendo = True
        self.consumido = True
        self.cond = threading.Condition()
        self.hilo = threading.Thread(target=self._bucle_captura, daemon=True)
        self.hilo.start()

    def _conectar(self):
        if self.cap is not None:
            self.cap.release()
        self.cap = abrir_captura_rapida(self.url)

    def _bucle_captura(self):
        self._conectar()
        fallos = 0
        t_ultimo = time.time()
        while self.corriendo:
            if self.cap is None or not self.cap.isOpened():
                self.conectado = False
                time.sleep(2)
                self._conectar()
                t_ultimo = time.time()
                continue

            # Watchdog: imagen congelada -> reconectar para obtener un I-frame nuevo
            if time.time() - t_ultimo > SEGUNDOS_CONGELADO:
                self._conectar()
                t_ultimo = time.time()
                fallos = 0
                continue

            if self.cap.grab():
                fallos = 0
                t_ultimo = time.time()
                if self.consumido:
                    ret, frame = self.cap.retrieve()
                    if ret and frame is not None:
                        with self.cond:
                            self.conectado = True
                            self.frame = frame
                            self.frame_id += 1
                            self.consumido = False
                            self.cond.notify_all()
                else:
                    self.conectado = True
            else:
                fallos += 1
                time.sleep(0.01)
                if fallos >= 80:
                    self.conectado = False
                    fallos = 0
                    self._conectar()

    def leer_si_nuevo(self, ultimo_id, espera=0.03):
        # Espera a que llegue un cuadro nuevo en vez de dormir fijo
        with self.cond:
            if self.frame_id == ultimo_id and self.conectado:
                self.cond.wait(timeout=espera)
            if not self.conectado or self.frame is None:
                return "desconectado", None, ultimo_id
            if self.frame_id == ultimo_id:
                return "sin_cambios", None, ultimo_id
            self.consumido = True
            return "nuevo", self.frame, self.frame_id

    def detener(self):
        self.corriendo = False
        self.hilo.join(timeout=1.5)
        if self.cap is not None:
            self.cap.release()


def pantalla_espera(texto="Conectando con la camara..."):
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.putText(img, texto, (360, 360), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (220, 220, 220), 2, cv2.LINE_AA)
    return img


def main():
    ventana = "Monitor Steren 24/7 (Doble clic = Pantalla completa | Q/ESC = Salir)"
    cv2.namedWindow(ventana, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(ventana, 1280, 720)

    estado_ui = {"pantalla_completa": False}

    def alternar_pantalla_completa():
        estado_ui["pantalla_completa"] = not estado_ui["pantalla_completa"]
        modo = cv2.WINDOW_FULLSCREEN if estado_ui["pantalla_completa"] else cv2.WINDOW_NORMAL
        cv2.setWindowProperty(ventana, cv2.WND_PROP_FULLSCREEN, modo)

    def evento_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDBLCLK:
            alternar_pantalla_completa()

    cv2.setMouseCallback(ventana, evento_mouse)
    cv2.imshow(ventana, pantalla_espera("Iniciando flujo HD..."))
    cv2.waitKey(1)

    url_hd = obtener_ruta_hd()
    monitor = Monitor247(url_hd)
    ultimo_id = -1

    while True:
        estado, frame, ultimo_id = monitor.leer_si_nuevo(ultimo_id)
        if estado == "nuevo":
            if not estado_ui["pantalla_completa"] and frame.shape[1] > 1280:
                h, w = frame.shape[:2]
                frame_mostrar = cv2.resize(frame, (1280, int(h * (1280 / w))), interpolation=cv2.INTER_LINEAR)
            else:
                frame_mostrar = frame
            cv2.imshow(ventana, frame_mostrar)
        elif estado == "desconectado":
            cv2.imshow(ventana, pantalla_espera("Senal perdida. Reconectando automaticamente..."))
            time.sleep(0.05)

        tecla = cv2.waitKey(1) & 0xFF
        if tecla in (ord("q"), ord("Q"), 27):
            break
        elif tecla in (ord("f"), ord("F")):
            alternar_pantalla_completa()

        if cv2.getWindowProperty(ventana, cv2.WND_PROP_VISIBLE) < 1:
            break

    monitor.detener()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()