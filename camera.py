from picamera2 import Picamera2
 
from PIL import Image
 
import threading
 
import time
 
import io
 
import numpy as np
 
 
# Resolución reducida: 640x480 es suficiente y libera mucha CPU vs 1280x720
 
CAPTURE_W, CAPTURE_H = 640, 480
 
TARGET_FPS   = 15       # cap de FPS para no saturar la CPU del Pi
 
MOTION_EVERY = 3        # analizar movimiento cada N frames (no en cada uno)
 
 
class CameraManager:
 
    def __init__(self):
 
        self.picam2 = Picamera2(0)
 
        config = self.picam2.create_preview_configuration(
 
            main={"size": (CAPTURE_W, CAPTURE_H), "format": "RGB888"}
 
        )
 
        self.picam2.configure(config)
 
        self.picam2.start()
 
        time.sleep(2)
 
        print(f"✅ Cámara lista {CAPTURE_W}x{CAPTURE_H} @ {TARGET_FPS}fps")
 
        self.frame      = None
 
        self.lock       = threading.Lock()
 
        self._running   = True
 
        self._frame_interval = 1.0 / TARGET_FPS  # ~66ms entre frames
 
        # Detección de cambio
 
        self._prev_gray        = None
 
        self._change_callbacks = []
 
        self._change_lock      = threading.Lock()
 
        self._cooldown         = False
 
        self._frame_count      = 0
 
        # ── Parámetros ───────────────────────────────────────────────────────
 
        self._cooldown_secs = 120.0    # mínimo entre detecciones
 
        self.DIFF_THRESHOLD = 80     # sensibilidad por pixel (0-255)
 
        self.DIFF_RATIO     = 0.10  # % de píxeles que deben cambiar (1.5%)
 
        # ─────────────────────────────────────────────────────────────────────
 
        threading.Thread(target=self._loop, daemon=True).start()
 
    # ── Loop principal ─────────────────────────────────────────────────────────
 
    def _loop(self):
 
        _interval = self._frame_interval
 
        while self._running:
 
            t0  = time.monotonic()
 
            raw = self.picam2.capture_array()
 
            # RGB888 puede venir como BGR según el driver → invertir canales
 
            imagen = Image.fromarray(raw[:, :, ::-1])
 
            with self.lock:
 
                self.frame = imagen
 
            # ── Detección de movimiento solo cada MOTION_EVERY frames ────────
 
            self._frame_count += 1
 
            if self._frame_count % MOTION_EVERY == 0:
 
                # np.dot es ~3x más rápido que np.mean para escala de grises
 
                gray = np.dot(raw[..., :3], [0.299, 0.587, 0.114]).astype(np.uint8)
 
                with self._change_lock:
 
                    if self._prev_gray is not None and not self._cooldown:
 
                        diff  = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16))
 
                        ratio = np.sum(diff > self.DIFF_THRESHOLD) / gray.size
 
                        if ratio > self.DIFF_RATIO:
 
                            print(f"🔔 Movimiento {ratio:.2%} — enviando a Gemini...")
 
                            self._activate_cooldown()
 
                            self._notify_callbacks(imagen.copy())
 
                    self._prev_gray = gray
 
            # ── Limitar FPS para no saturar la CPU ───────────────────────────
 
            elapsed = time.monotonic() - t0
 
            wait    = _interval - elapsed
 
            if wait > 0:
 
                time.sleep(wait)
 
    # ── Cooldown ───────────────────────────────────────────────────────────────
 
    def _activate_cooldown(self):
 
        self._cooldown = True
 
        t = threading.Timer(self._cooldown_secs, self._reset_cooldown)
 
        t.daemon = True
 
        t.start()
 
    def _reset_cooldown(self):
 
        with self._change_lock:
 
            self._cooldown = False
 
    # ── Callbacks ──────────────────────────────────────────────────────────────
 
    def on_change(self, callback):
 
        """Registra callback(imagen: PIL.Image) llamado al detectar cambio."""
 
        self._change_callbacks.append(callback)
 
    def _notify_callbacks(self, imagen: Image.Image):
 
        """Llama a cada callback en su propio hilo para no bloquear el loop."""
 
        for cb in self._change_callbacks:
 
            threading.Thread(target=cb, args=(imagen,), daemon=True).start()
 
    # ── Acceso a frames ────────────────────────────────────────────────────────
 
    def get_frame(self) -> Image.Image | None:
 
        with self.lock:
 
            return self.frame.copy() if self.frame is not None else None
 
    def get_frame_jpeg(self) -> bytes | None:
 
        imagen = self.get_frame()
 
        if imagen is None:
 
            return None
 
        buffer = io.BytesIO()
 
        imagen.save(buffer, format="JPEG", quality=85)
 
        return buffer.getvalue()
 
    def stop(self):
 
        self._running = False
 
        self.picam2.stop()
 
 
camera = CameraManager()