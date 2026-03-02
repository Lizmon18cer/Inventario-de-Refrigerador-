import io
import wave
import json
import re
import sounddevice as sd
import numpy as np
from google import genai
from google.genai import types
import os
 
_API_KEY = os.environ.get("GEMINI_API_KEY") or open('/home/nebula/frutas_api/gemini_service.py').read().split('"')[1] if 'GEMINI' not in os.environ else os.environ['GEMINI_API_KEY']
 
# Leer la key del gemini_service existente
import importlib.util, sys
spec = importlib.util.spec_from_file_location("gs", "/home/nebula/frutas_api/gemini_service.py")
gs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gs)
client = gs.client
MODEL  = gs.MODEL
 
SAMPLE_RATE = 16000
CHANNELS    = 1
DTYPE       = "int16"
 
def listar_dispositivos():
    devs = sd.query_devices()
    return [{"index":i,"name":d["name"],"inputs":d["max_input_channels"]} for i,d in enumerate(devs) if d["max_input_channels"]>0]
 
def encontrar_usb_mic():
    for d in listar_dispositivos():
        name = d["name"].lower()
        if any(k in name for k in ["usb","waveshare","audio adapter","card","c-media","usb audio"]):
            print(f"🎙️  USB mic: [{d['index']}] {d['name']}")
            return d["index"]
    print("⚠️  Usando dispositivo por defecto")
    return None
 
def grabar_audio(segundos=5, device_index=None):
    if device_index is None:
        device_index = encontrar_usb_mic()
    print(f"🔴 Grabando {segundos}s en device={device_index}...")
    audio = sd.rec(int(SAMPLE_RATE*segundos), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE, device=device_index, blocking=True)
    print("⏹️  Listo")
    buf = io.BytesIO()
    with wave.open(buf,"wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()
 
def transcribir_y_corregir(audio_wav, inventario_actual):
    try:
        inv_str = json.dumps(inventario_actual, ensure_ascii=False)
        prompt = f"""Escucha el audio. El usuario corrige el inventario del refrigerador.
Inventario actual: {inv_str}
Responde SOLO JSON sin markdown:
Si corrige cantidad: {{"accion":"corregir_cantidad","nombre":"producto","cantidad_nueva":N,"transcripcion":"lo que dijo","mensaje":"✏️ Corregido: N productos"}}
Si agrega: {{"accion":"agregar","nombre":"producto","categoria":"fruta|verdura|lacteo|bebida|otro","cantidad":N,"transcripcion":"lo que dijo","mensaje":"➕ Agregado"}}
Si elimina: {{"accion":"eliminar","nombre":"producto","transcripcion":"lo que dijo","mensaje":"🗑️ Eliminado"}}
Si confirma: {{"accion":"confirmar","transcripcion":"lo que dijo","mensaje":"✅ Confirmado"}}
Si no entiendes: {{"accion":"no_entendido","transcripcion":"lo que dijo","mensaje":"❓ No entendí"}}"""
        resp = client.models.generate_content(model=MODEL, contents=[prompt, types.Part.from_bytes(data=audio_wav, mime_type="audio/wav")])
        texto = resp.text.strip().replace("```json","").replace("```","").strip()
        print(f"🎙️  {texto[:200]}")
        m = re.search(r'\{.*\}', texto, re.DOTALL)
        if m:
            return json.loads(m.group())
        return {"accion":"no_entendido","transcripcion":texto,"mensaje":"❓ No entendí"}
    except Exception as e:
        print(f"❌ Error audio: {e}")
        return {"accion":"error","mensaje":str(e)[:80]}