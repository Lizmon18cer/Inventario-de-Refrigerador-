"""
tts_service.py — Servicio de voz para el asistente del refrigerador.
Usa gTTS (Google Text-to-Speech) para generar audio y lo reproduce
por la bocina USB Waveshare.

Instalación:
    pip install gTTS --break-system-packages
    sudo apt install mpv -y
"""

import os
import tempfile
import threading
import subprocess

# ── Buscar dispositivo de audio USB Waveshare ─────────────────────────────────

def _encontrar_speaker_usb() -> str | None:
    """Busca el índice ALSA del speaker USB (card number)."""
    try:
        result = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            lower = line.lower()
            if any(k in lower for k in ["usb", "waveshare", "audio adapter", "c-media"]):
                # Extraer "card X"
                if "card" in lower:
                    card = line.split("card")[1].strip().split(":")[0].strip()
                    print(f"🔊 Speaker USB encontrado: card {card} — {line.strip()}")
                    return card
    except Exception as e:
        print(f"⚠️ Error buscando speaker: {e}")
    return None


_USB_CARD = _encontrar_speaker_usb()

# ── Volumen de audio ──────────────────────────────────────────────────────────
_VOLUME = 100  # Volumen por defecto (0-200)

def set_volume(volumen: int):
    """Establece el volumen de audio (0-200)."""
    global _VOLUME
    _VOLUME = max(0, min(200, volumen))
    print(f"🔊 Volumen establecido a {_VOLUME}%")

def hablar(texto: str, bloquear: bool = False, volumen: int = None):
    """
    Genera audio con gTTS y lo reproduce por la bocina USB.
    
    Args:
        texto: Lo que el asistente va a decir.
        bloquear: Si True, espera a que termine de hablar.
        volumen: Nivel de volumen (0-200). Si es None, usa el volumen global.
    """
    def _reproducir():
        try:
            from gtts import gTTS
            
            vol = volumen if volumen is not None else _VOLUME
            
            # Generar MP3 en archivo temporal
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tts = gTTS(text=texto, lang="es", tld="com.mx")
                tts.save(tmp.name)
                tmp_path = tmp.name

            # Reproducir con mpv (ligero y compatible)
            cmd = ["mpv", "--no-terminal", "--no-video", f"--volume={vol}", tmp_path]
            
            # Si encontramos el speaker USB, forzar la salida por ahí
            if _USB_CARD:
                cmd.insert(1, f"--audio-device=alsa/plughw:{_USB_CARD},0")
            
            subprocess.run(cmd, capture_output=True, timeout=30)
            
            # Limpiar archivo temporal
            os.unlink(tmp_path)
            
        except Exception as e:
            print(f"❌ Error TTS: {e}")
            # Fallback: usar espeak si gTTS falla
            try:
                cmd_espeak = ["espeak", "-v", "es", texto]
                subprocess.run(cmd_espeak, capture_output=True, timeout=15)
            except Exception:
                print(f"❌ Fallback espeak también falló")

    if bloquear:
        _reproducir()
    else:
        threading.Thread(target=_reproducir, daemon=True).start()


# ── Frases predefinidas del asistente ─────────────────────────────────────────

def decir_bienvenida():
    hablar("Asistente de refrigerador listo. Puedo ver lo que hay dentro y corregir el inventario con tu voz.")

def decir_escaneo(productos: list):
    """Anuncia los productos detectados."""
    if not productos:
        hablar("No detecté productos nuevos.")
        return
    
    total = sum(p.get("cantidad", 1) for p in productos)
    
    if len(productos) == 1:
        p = productos[0]
        cant = p.get("cantidad", 1)
        nombre = p.get("nombre", "producto")
        hablar(f"Detecté {cant} {nombre}.")
    elif len(productos) <= 5:
        partes = []
        for p in productos:
            cant = p.get("cantidad", 1)
            nombre = p.get("nombre", "producto")
            partes.append(f"{cant} {nombre}")
        lista = ", ".join(partes[:-1]) + f" y {partes[-1]}"
        hablar(f"Detecté {lista}.")
    else:
        hablar(f"Detecté {total} productos en {len(productos)} categorías.")

def decir_correccion(accion: dict):
    """Anuncia la corrección aplicada."""
    mensaje = accion.get("mensaje", "")
    a = accion.get("accion", "")
    
    if a == "corregir_cantidad":
        nombre = accion.get("nombre", "producto")
        cantidad = accion.get("cantidad_nueva", "")
        hablar(f"Listo, corregí {nombre} a {cantidad}.")
    elif a == "agregar":
        nombre = accion.get("nombre", "producto")
        cantidad = accion.get("cantidad", 1)
        hablar(f"Listo, agregué {cantidad} {nombre} al inventario.")
    elif a == "eliminar":
        nombre = accion.get("nombre", "producto")
        hablar(f"Listo, eliminé {nombre} del inventario.")
    elif a == "confirmar":
        hablar("Perfecto, el inventario queda como está.")
    elif a == "no_entendido":
        hablar("No entendí lo que dijiste. ¿Puedes repetirlo?")
    elif a == "error":
        hablar("Hubo un error al procesar tu voz. Intenta de nuevo.")
    else:
        hablar("Listo.")

def decir_inventario(inventario: list):
    """Lee el inventario completo en voz alta."""
    if not inventario:
        hablar("El inventario está vacío. No hay productos registrados.")
        return
    
    total = sum(p.get("cantidad", 1) for p in inventario)
    
    # Agrupar por categoría
    categorias = {}
    for p in inventario:
        cat = p.get("categoria", "otro")
        if cat not in categorias:
            categorias[cat] = []
        categorias[cat].append(p)
    
    nombres_cat = {
        "fruta": "frutas", "verdura": "verduras",
        "lacteo": "lácteos", "bebida": "bebidas", "otro": "otros"
    }
    
    partes = [f"Tienes {total} productos en total."]
    
    for cat, productos in categorias.items():
        nombre_cat = nombres_cat.get(cat, cat)
        items = []
        for p in productos:
            cant = p.get("cantidad", 1)
            nombre = p.get("nombre", "producto")
            items.append(f"{cant} {nombre}")
        lista = ", ".join(items)
        partes.append(f"En {nombre_cat}: {lista}.")
    
    hablar(" ".join(partes))

def decir_inventario_limpio():
    hablar("Listo, el inventario fue limpiado. Todo en cero.")

def decir_grabando():
    hablar("Te escucho, dime.")
