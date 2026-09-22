"""Скан с iPhone: HTTP-сервер + папка AirDrop (если Wi‑Fi блокирует устройства)."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger("cyberx.phone_scan")

ROOT = Path(__file__).resolve().parents[2]
INBOX = ROOT / "data" / "_phone_inbox"
# Удобно кидать AirDrop сюда (на Рабочий стол)
DESKTOP_INBOX = Path.home() / "Desktop" / "CyberX_Inbox"

PORTS = (8765, 8766, 8080, 5555, 9999)

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no"/>
<title>CyberX скан</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family:-apple-system,system-ui,sans-serif; background:#070708; color:#fff; }
  header { padding:14px 16px; background:#121214; border-bottom:2px solid #FF1E1A; }
  h1 { margin:0; font-size:18px; color:#FF1E1A; }
  .hint { margin-top:8px; font-size:15px; line-height:1.35; white-space:pre-wrap; }
  .box { padding:12px 16px; }
  video, canvas { width:100%; max-height:55vh; background:#000; border-radius:12px; object-fit:cover; }
  .row { display:flex; gap:10px; margin-top:12px; }
  button {
    flex:1; padding:16px 12px; border:0; border-radius:12px; font-size:17px; font-weight:700;
    background:#FF1E1A; color:#fff;
  }
  button.secondary { background:#242428; }
  button:disabled { opacity:.45; }
  .status { margin-top:12px; font-size:14px; color:#E8E8EA; min-height:1.4em; }
  .ok { color:#3DDC84; }
  .err { color:#FF6B6B; }
  input[type=file] { display:none; }
</style>
</head>
<body>
<header>
  <h1>CyberX · скан с iPhone</h1>
  <div class="hint" id="hint">Загрузка подсказки…</div>
</header>
<div class="box">
  <video id="v" playsinline autoplay muted></video>
  <canvas id="c" style="display:none"></canvas>
  <div class="row">
    <button id="shot" type="button">СНЯТЬ</button>
    <button id="pick" class="secondary" type="button">Из Фото</button>
  </div>
  <input id="file" type="file" accept="image/*" capture="environment"/>
  <div class="status" id="st">Разрешите камеру Safari</div>
</div>
<script>
const st = document.getElementById('st');
const hintEl = document.getElementById('hint');
const v = document.getElementById('v');
const c = document.getElementById('c');
const shot = document.getElementById('shot');
const pick = document.getElementById('pick');
const file = document.getElementById('file');

async function refreshHint() {
  try {
    const r = await fetch('/api/hint');
    const j = await r.json();
    hintEl.textContent = j.hint || 'Наведите упаковку';
  } catch (e) { hintEl.textContent = 'Нет связи с Mac'; }
}
setInterval(refreshHint, 1500);
refreshHint();

async function startCam() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } }
    });
    v.srcObject = stream;
    st.textContent = 'Камера OK — наведите товар и нажмите СНЯТЬ';
  } catch (e) {
    st.className = 'status err';
    st.textContent = 'Камера недоступна. Нажмите «Из Фото» и выберите снимок.';
  }
}
startCam();

async function uploadBlob(blob) {
  st.className = 'status';
  st.textContent = 'Отправка на Mac…';
  shot.disabled = true;
  try {
    const fd = new FormData();
    fd.append('photo', blob, 'iphone_' + Date.now() + '.jpg');
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || 'upload fail');
    st.className = 'status ok';
    st.textContent = '✓ На Mac · ' + (j.message || 'откройте Desktop');
    await refreshHint();
  } catch (e) {
    st.className = 'status err';
    st.textContent = 'Ошибка: ' + e.message;
  } finally {
    shot.disabled = false;
  }
}

shot.onclick = async () => {
  if (!v.videoWidth) { st.textContent = 'Камера ещё не готова'; return; }
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0);
  const blob = await new Promise(res => c.toBlob(res, 'image/jpeg', 0.92));
  if (blob) await uploadBlob(blob);
};

pick.onclick = () => file.click();
file.onchange = async () => {
  const f = file.files && file.files[0];
  if (!f) return;
  await uploadBlob(f);
  file.value = '';
};
</script>
</body>
</html>
"""


def lan_ips() -> List[str]:
    """Все локальные IPv4 (кроме loopback), Wi‑Fi обычно первый полезный."""
    found: List[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
        s.close()
        if primary and not primary.startswith("127."):
            found.append(primary)
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            if ip not in found:
                found.append(ip)
    except Exception:
        pass
    # ifconfig fallback
    try:
        out = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet ") and "127.0.0.1" not in line:
                ip = line.split()[1]
                if ip not in found and not ip.startswith("169.254."):
                    found.append(ip)
    except Exception:
        pass
    return found or ["127.0.0.1"]


def lan_ip() -> str:
    return lan_ips()[0]


def bonjour_host() -> str:
    try:
        name = socket.gethostname().split(".")[0]
        return f"{name}.local"
    except Exception:
        return "mac.local"


def candidate_urls(port: int) -> List[str]:
    urls = [f"http://{ip}:{port}/" for ip in lan_ips()]
    urls.append(f"http://{bonjour_host()}:{port}/")
    # unique preserve order
    out: List[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def ensure_inboxes() -> Path:
    INBOX.mkdir(parents=True, exist_ok=True)
    DESKTOP_INBOX.mkdir(parents=True, exist_ok=True)
    readme = DESKTOP_INBOX / "КАК_СЮДА_КИДАТЬ.txt"
    if not readme.exists():
        readme.write_text(
            "AirDrop фото с iPhone в ЭТУ папку (CyberX_Inbox на Рабочем столе).\n"
            "В Desktop должен быть включён режим iPhone / скан — фото подхватится само.\n",
            encoding="utf-8",
        )
    return DESKTOP_INBOX


def open_inbox_in_finder() -> None:
    path = ensure_inboxes()
    try:
        subprocess.Popen(["open", str(path)])
    except Exception:
        pass


class InboxWatcher:
    """Следит за AirDrop-папкой и data/_phone_inbox."""

    def __init__(self, on_image: Callable[[Path], None], interval: float = 0.8):
        self._on_image = on_image
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: set[str] = set()

    def start(self) -> None:
        ensure_inboxes()
        # уже существующие не трогаем
        for folder in (DESKTOP_INBOX, INBOX):
            for p in folder.iterdir():
                if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".webp"}:
                    self._seen.add(str(p.resolve()))
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for folder in (DESKTOP_INBOX, INBOX):
                    if not folder.exists():
                        continue
                    for p in sorted(folder.iterdir(), key=lambda x: x.stat().st_mtime):
                        if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".heic", ".webp", ".JPG"}:
                            continue
                        key = str(p.resolve())
                        if key in self._seen:
                            continue
                        # файл ещё пишется?
                        try:
                            sz1 = p.stat().st_size
                            time.sleep(0.25)
                            sz2 = p.stat().st_size
                            if sz1 != sz2 or sz2 < 1000:
                                continue
                        except Exception:
                            continue
                        self._seen.add(key)
                        # HEIC → JPG
                        out = p
                        if p.suffix.lower() in {".heic", ".heif"}:
                            conv = INBOX / f"from_airdrop_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
                            try:
                                try:
                                    from pillow_heif import register_heif_opener

                                    register_heif_opener()
                                except Exception:
                                    pass
                                from PIL import Image

                                Image.open(p).convert("RGB").save(conv, quality=92)
                                out = conv
                            except Exception:
                                logger.warning(
                                    "HEIC не открылся (%s). В iPhone: Настройки→Камера→Форматы→«Наиболее совместимый»",
                                    p.name,
                                )
                                continue
                        try:
                            self._on_image(out)
                        except Exception:
                            logger.exception("inbox callback")
            except Exception:
                logger.exception("inbox watcher")
            self._stop.wait(self._interval)


class PhoneScanServer:
    def __init__(self, port: int = 8765):
        self.port = int(port)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._hint = "Выберите SKU на Mac, затем снимайте ракурс."
        self._on_image: Optional[Callable[[Path], None]] = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{lan_ip()}:{self.port}/"

    def urls(self) -> List[str]:
        return candidate_urls(self.port)

    def set_hint(self, text: str) -> None:
        with self._lock:
            self._hint = text or self._hint

    def get_hint(self) -> str:
        with self._lock:
            return self._hint

    def set_callback(self, cb: Optional[Callable[[Path], None]]) -> None:
        self._on_image = cb

    def is_running(self) -> bool:
        return self._httpd is not None

    def start(self, on_image: Callable[[Path], None], hint: str = "") -> str:
        if self.is_running():
            self.set_callback(on_image)
            if hint:
                self.set_hint(hint)
            return self.url
        self.set_callback(on_image)
        if hint:
            self.set_hint(hint)
        ensure_inboxes()
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # noqa: N802
                logger.debug("phone_scan: " + fmt, *args)

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                from urllib.parse import urlparse

                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    self._send(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/hint":
                    payload = json.dumps({"hint": hub.get_hint()}, ensure_ascii=False).encode("utf-8")
                    self._send(200, payload, "application/json; charset=utf-8")
                    return
                if path == "/api/ping":
                    self._send(200, b'{"ok":true}', "application/json")
                    return
                self._send(404, b"not found", "text/plain")

            def do_POST(self):  # noqa: N802
                from urllib.parse import urlparse

                path = urlparse(self.path).path
                if path != "/api/upload":
                    self._send(404, b"{}", "application/json")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length)
                    ctype = self.headers.get("Content-Type", "")
                    data = _extract_multipart_file(raw, ctype)
                    if not data:
                        raise ValueError("нет файла photo")
                    dest = INBOX / f"iphone_{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
                    dest.write_bytes(data)
                    cb = hub._on_image
                    if cb:
                        cb(dest)
                    msg = json.dumps(
                        {"ok": True, "message": hub.get_hint()},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self._send(200, msg, "application/json; charset=utf-8")
                except Exception as exc:
                    logger.exception("upload failed")
                    msg = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode(
                        "utf-8"
                    )
                    self._send(400, msg, "application/json; charset=utf-8")

        last_err: Optional[Exception] = None
        for port in (self.port, *PORTS):
            try:
                httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
                self.port = port
                self._httpd = httpd
                self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                self._thread.start()
                logger.info("Phone scan server: %s", self.url)
                return self.url
            except OSError as exc:
                last_err = exc
                continue
        raise OSError(f"Не удалось открыть порты {PORTS}: {last_err}")

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
        self._httpd = None
        self._thread = None
        self._on_image = None


def _extract_multipart_file(body: bytes, content_type: str) -> Optional[bytes]:
    if "multipart/form-data" not in content_type:
        if body[:3] == b"\xff\xd8\xff" or body[:8] == b"\x89PNG\r\n\x1a\n":
            return body
        return None
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            boundary = part.split("=", 1)[1].strip().strip('"')
            break
    if not boundary:
        return None
    token = b"--" + boundary.encode("ascii", errors="ignore")
    chunks = body.split(token)
    for chunk in chunks:
        if b"Content-Disposition" not in chunk:
            continue
        if b"filename=" not in chunk and b'name="photo"' not in chunk:
            continue
        sep = b"\r\n\r\n"
        i = chunk.find(sep)
        if i < 0:
            continue
        data = chunk[i + len(sep) :]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        if data.endswith(b"--"):
            data = data[:-2]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        if data:
            return data
    return None


def make_qr_image(url: str, size: int = 280):
    from PIL import Image, ImageDraw

    try:
        import qrcode

        qr = qrcode.QRCode(version=None, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        return img.resize((size, size))
    except Exception:
        img = Image.new("RGB", (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle((8, 8, size - 8, size - 8), outline=(0, 0, 0), width=3)
        text = url
        y = size // 2 - 20
        for i in range(0, len(text), 22):
            draw.text((16, y), text[i : i + 22], fill=(0, 0, 0))
            y += 18
        return img
