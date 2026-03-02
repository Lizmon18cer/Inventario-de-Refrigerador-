from fastapi import FastAPI, WebSocket, WebSocketDisconnect
 
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
 
import asyncio, json
 
from PIL import Image
 
from camera import camera
 
from gemini_service import identificar_productos
 
from audio_service import grabar_audio, transcribir_y_corregir, listar_dispositivos, encontrar_usb_mic
 
from tts_service import (
    hablar, decir_bienvenida, decir_escaneo, decir_correccion,
    decir_inventario, decir_inventario_limpio, decir_grabando, set_volume
)
app = FastAPI(title="Inventario Refrigerador")
 
# ── Estado global ──────────────────────────────────────────────────────────────
 
inventario_acumulado: dict = {}
 
ultimo_resultado    = {"detectados": [], "total": 0, "resumen": "Sin análisis aún"}

ultimo_scan_detectado = None  # Para detectar cambios

 
ultima_correccion   = None
 
_main_loop: asyncio.AbstractEventLoop = None
 
_grabando           = False   # semáforo para evitar grabaciones paralelas

_puerta_abierta     = False   # estado de puerta (abierta/cerrada)
 
# ── WebSocket manager ─────────────────────────────────────────────────────────
 
class ConnectionManager:
 
    def __init__(self):
 
        self.active: list[WebSocket] = []
 
    async def connect(self, ws: WebSocket):
 
        await ws.accept()
 
        self.active.append(ws)
 
    def disconnect(self, ws: WebSocket):
 
        if ws in self.active:
 
            self.active.remove(ws)
 
    async def broadcast(self, data: dict):
 
        msg = json.dumps(data, ensure_ascii=False)
 
        dead = []
 
        for ws in self.active:
 
            try:
 
                await ws.send_text(msg)
 
            except Exception:
 
                dead.append(ws)
 
        for ws in dead:
 
            self.disconnect(ws)
 
ws_manager = ConnectionManager()
 

# ── Función para detectar cambios en productos detectados ────────────────────
def _hay_cambios_detectados(nuevo_resultado: dict) -> bool:
    """Detecta si hay cambios en los productos detectados."""
    global ultimo_scan_detectado
    
    nuevos_detectados = nuevo_resultado.get("detectados", [])
    
    # Si no hay un scan anterior, es un cambio
    if ultimo_scan_detectado is None:
        return True
    
    anteriores = ultimo_scan_detectado.get("detectados", [])
    
    # Si cantidad de productos es diferente, hay cambio
    if len(nuevos_detectados) != len(anteriores):
        return True
    
    # Crear sets de nombres para comparar
    nuevos_nombres = set(p["nombre"].lower() for p in nuevos_detectados)
    anteriores_nombres = set(p["nombre"].lower() for p in anteriores)
    
    # Si los nombres son diferentes, hay cambio
    if nuevos_nombres != anteriores_nombres:
        return True
    
    # Comparar cantidades
    for nuevo in nuevos_detectados:
        nombre_nuevo = nuevo["nombre"].lower()
        cant_nueva = nuevo.get("cantidad", 1)
        
        # Buscar el producto anterior
        anterior = next((p for p in anteriores if p["nombre"].lower() == nombre_nuevo), None)
        if anterior and anterior.get("cantidad", 1) != cant_nueva:
            return True
    
    return False

 
def _actualizar_inventario(result: dict, resultado_anterior: dict = None) -> dict:

    """Actualiza inventario detectando agregados y removidos.
    
    result: nuevo resultado del scan
    resultado_anterior: resultado anterior para detectar remociones
    
    Retorna: {removidos: [...], nuevos: [...], modificados: [...]}
    """
 
    global ultimo_resultado
 
    ultimo_resultado = result
    
    cambios = {"removidos": [], "nuevos": [], "modificados": []}
    
    # Obtener productos actuales
    productos_actuales = {p["nombre"].lower(): p for p in result.get("detectados", [])}
    
    # Obtener productos anteriores si existen
    productos_anteriores = {}
    if resultado_anterior:
        productos_anteriores = {p["nombre"].lower(): p for p in resultado_anterior.get("detectados", [])}
    
    # ── Detectar productos REMOVIDOS ────────────────────────────────────────
    for nombre_anterior in productos_anteriores:
        if nombre_anterior not in productos_actuales:
            # Producto desapareció: fue sacado del refrigerador
            producto_anterior = productos_anteriores[nombre_anterior]
            print(f"🗑️  Removido: {producto_anterior['nombre']} x{producto_anterior.get('cantidad', 1)}")
            
            cambios["removidos"].append(producto_anterior)
            
            # Buscar y remover del inventario acumulado
            keys_to_delete = [k for k in inventario_acumulado.keys() 
                             if nombre_anterior in k.split('_')[0].lower()]
            
            for k in keys_to_delete:
                print(f"   ❌ Eliminado del inventario: {inventario_acumulado[k]['nombre']}")
                del inventario_acumulado[k]
    
    # ── Detectar productos NUEVOS o MODIFICADOS ────────────────────────────
    for producto in result.get("detectados", []):
 
        key = (
 
            f"{producto['nombre'].lower()}_"
 
            f"{(producto.get('marca') or 'sin_marca').lower()}_"
 
            f"{producto['categoria']}"
 
        )
        
        nombre_lower = producto['nombre'].lower()
        
        # Si es un producto nuevo (no estaba antes)
        if nombre_lower not in productos_anteriores:
            print(f"➕ Nuevo: {producto['nombre']} x{producto.get('cantidad', 1)}")
            cambios["nuevos"].append(producto)
            inventario_acumulado[key] = {**producto, "visto": 1, "corregido": False}
        else:
            # Producto que ya estaba: actualizar cantidad si cambió
            anterior = productos_anteriores[nombre_lower]
            cant_anterior = anterior.get("cantidad", 1)
            cant_actual = producto.get("cantidad", 1)
            
            if key in inventario_acumulado:
                if cant_actual != cant_anterior:
                    # Cambió la cantidad
                    diff = cant_actual - cant_anterior
                    if diff > 0:
                        print(f"➕ Agregado: {producto['nombre']} (+{diff})")
                        cambios["modificados"].append({
                            "nombre": producto['nombre'],
                            "tipo": "agregado",
                            "cantidad": diff
                        })
                    else:
                        print(f"➖ Sacado: {producto['nombre']} ({diff})")
                        cambios["modificados"].append({
                            "nombre": producto['nombre'],
                            "tipo": "sacado",
                            "cantidad": abs(diff)
                        })
                    
                    inventario_acumulado[key]["cantidad"] = cant_actual
                    inventario_acumulado[key]["visto"] += 1
                else:
                    # Cantidad igual, solo aumentar visto
                    inventario_acumulado[key]["visto"] += 1
    
    return cambios
 
 
def _aplicar_correccion(accion: dict) -> bool:
 
    """Aplica la corrección de voz al inventario. Devuelve True si tuvo efecto."""
 
    a = accion.get("accion")
 
    nombre = accion.get("nombre", "").lower().strip()
 
    print(f"🔧 Aplicando corrección: {a} — {nombre}")
 
    if a == "corregir_cantidad":
 
        # Buscar el producto por nombre (búsqueda flexible)
 
        for key, prod in inventario_acumulado.items():
 
            if nombre in prod["nombre"].lower():
 
                cantidad_nueva = accion.get("cantidad_nueva", prod["cantidad"])
 
                print(f"   ✏️  {prod['nombre']}: {prod['cantidad']} → {cantidad_nueva}")
 
                prod["cantidad"] = cantidad_nueva
 
                prod["corregido"] = True  # Marca que fue corregido
 
                return True
 
        print(f"   ⚠️  Producto no encontrado: {nombre}")
 
        return False
 
    elif a == "agregar":
 
        key = f"{nombre}_sin_marca_{accion.get('categoria','otro')}"
 
        cantidad = accion.get("cantidad", 1)
 
        if key in inventario_acumulado:
 
            inventario_acumulado[key]["cantidad"] += cantidad
 
            print(f"   ➕ Agregada cantidad: {key}")
 
        else:
 
            inventario_acumulado[key] = {
 
                "nombre":      accion.get("nombre", nombre),
 
                "marca":       None,
 
                "categoria":   accion.get("categoria", "otro"),
 
                "estado":      "bueno",
 
                "cantidad":    cantidad,
 
                "descripcion": "Agregado por voz",
 
                "visto":       1,
 
                "corregido":   True
 
            }
 
            print(f"   ✨ Nuevo producto: {key}")
 
        return True
 
    elif a == "eliminar":
 
        keys_to_delete = [k for k,v in inventario_acumulado.items() if nombre in v["nombre"].lower()]
 
        for k in keys_to_delete:
 
            print(f"   🗑️  Eliminado: {inventario_acumulado[k]['nombre']}")
 
            del inventario_acumulado[k]
 
        return len(keys_to_delete) > 0
 
    elif a == "confirmar":
 
        print(f"   ✅ Confirmado")
 
        return True  # solo confirma, no modifica
 
    return False
 
 
async def _escanear_y_broadcast(origen: str = "") -> bool:

    """Escanea y solo broadcast si hay cambios. Retorna True si hubo cambios."""

    global ultimo_scan_detectado
    
    imagen = camera.get_frame()
 
    if imagen is None:
 
        return False
 
    loop   = asyncio.get_event_loop()
 
    result = await loop.run_in_executor(None, identificar_productos, imagen)
 
    if result is None:
 
        return False
    
    # Detectar si hay cambios
    hay_cambios = _hay_cambios_detectados(result)
    
    if not hay_cambios:
        print(f"📸 [{origen}] Sin cambios detectados")
        return False
    
    # Actualizar inventario y obtener cambios detectados
    cambios = _actualizar_inventario(result, ultimo_scan_detectado)
    
    # Actualizar el último scan detectado DESPUÉS de procesar cambios
    ultimo_scan_detectado = result
    
    inv   = list(inventario_acumulado.values())
 
    total = sum(p["cantidad"] for p in inv)
    
    # Generar mensajes de notificación por voz
    mensajes_voz = []
    if cambios["removidos"]:
        removidos_str = ", ".join(f"{p.get('cantidad', 1)} {p['nombre']}" for p in cambios["removidos"])
        mensajes_voz.append(f"Se han sacado: {removidos_str}")
    
    if cambios["nuevos"]:
        nuevos_str = ", ".join(f"{p.get('cantidad', 1)} {p['nombre']}" for p in cambios["nuevos"])
        mensajes_voz.append(f"Se agregaron: {nuevos_str}")
 
    await ws_manager.broadcast({
 
        "tipo":       "actualizacion",
 
        "resultado":  result,
 
        "inventario": inv,
 
        "total":      total,
        
        "cambios":    cambios
 
    })
    
    # Anunciar cambios por voz (en background, sin bloquear)
    if mensajes_voz:
        mensaje_completo = ". ".join(mensajes_voz)
        hablar(mensaje_completo, bloquear=False)
 
    print(f"📸 [{origen}] {len(result.get('detectados',[]))} productos en cámara (CAMBIO DETECTADO)")
    
    return True
 
 
# ── Scan periódico ────────────────────────────────────────────────────────────
 
async def _periodic_scan():
 
    await asyncio.sleep(1)
 
    while True:
 
        if _puerta_abierta:
 
            await _escanear_y_broadcast("deteccion_automatica")
 
        await asyncio.sleep(2)  # Revisar cada 2 segundos
 
 
# ── Startup ───────────────────────────────────────────────────────────────────
 
@app.on_event("startup")
 
async def startup():
 
    global _main_loop
 
    _main_loop = asyncio.get_event_loop()
 
    asyncio.create_task(_periodic_scan())
 
    # Mostrar micrófonos disponibles al arrancar
 
    devs = listar_dispositivos()
 
    print("🎙️  Micrófonos disponibles:")
 
    for d in devs:
 
        print(f"   [{d['index']}] {d['name']} ({d['inputs']} ch)")
 
    usb = encontrar_usb_mic()
 
    print(f"🎙️  USB mic index: {usb}")
 
    print("🚀 Servidor listo — PUERTA + VOZ + periódico 60s")
 
    decir_bienvenida()
 
 
# ── WebSocket ─────────────────────────────────────────────────────────────────
 
@app.websocket("/ws")
 
async def websocket_endpoint(ws: WebSocket):
 
    await ws_manager.connect(ws)
 
    inv = list(inventario_acumulado.values())
 
    await ws.send_text(json.dumps({
 
        "tipo":       "estado_inicial",
 
        "resultado":  ultimo_resultado,
 
        "inventario": inv,
 
        "total":      sum(p["cantidad"] for p in inv),
 
        "correccion": ultima_correccion
 
    }, ensure_ascii=False))
 
    try:
 
        while True:
 
            await ws.receive_text()
 
    except WebSocketDisconnect:
 
        ws_manager.disconnect(ws)
 
 
# ── Stream MJPEG ──────────────────────────────────────────────────────────────
 
def generar_frames():
 
    while True:
 
        jpeg = camera.get_frame_jpeg()
 
        if jpeg is None:
 
            continue
 
        yield (b'--frame\r\n'
 
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n')
 
@app.get("/stream")
 
def stream():
 
    return StreamingResponse(generar_frames(),
 
                              media_type="multipart/x-mixed-replace; boundary=frame")
 
 

# ── Endpoints ─────────────────────────────────────────────────────────────────
 
@app.get("/set_puerta")
 
async def set_puerta(abierta: bool = False):
 
    """Establece el estado de la puerta (abierta/cerrada)."""
 
    global _puerta_abierta
 
    _puerta_abierta = abierta
 
    print(f"🚪 Puerta: {'ABIERTA ✅' if abierta else 'CERRADA 🚫'}")
 
    if abierta:
 
        # Escanear inmediatamente al abrir
 
        await _escanear_y_broadcast("puerta_abierta")
 
    return {"status": "ok", "puerta_abierta": abierta}
 
 
@app.get("/identificar")
 
async def identificar():
 
    await _escanear_y_broadcast("manual")
 
    return {"status": "ok", "resultado": ultimo_resultado}
 
 
@app.get("/corregir")
 
async def corregir(segundos: float = 5.0):
    """
 
    Graba {segundos}s del micrófono USB, interpreta la corrección de voz
 
    y actualiza el inventario en tiempo real vía WebSocket.
 
    """
 
    global _grabando, ultima_correccion
 
    if _grabando:
 
        return JSONResponse({"error": "Ya hay una grabación en curso"}, status_code=409)
 
    _grabando = True
 
    inv_actual = list(inventario_acumulado.values())
 
    try:
 
        # Notificar UI que empieza la grabación
 
        await ws_manager.broadcast({"tipo": "grabando", "segundos": segundos})
 
        decir_grabando()  # dice "Te escucho, dime"
 
        # Grabar en executor para no bloquear el event loop
 
        loop     = asyncio.get_event_loop()
 
        usb_idx  = encontrar_usb_mic()
 
        audio_bytes = await loop.run_in_executor(
 
            None, lambda: grabar_audio(segundos, usb_idx)
 
        )
 
        # Notificar UI que se está procesando
 
        await ws_manager.broadcast({"tipo": "procesando_voz"})
 
        # Transcribir y obtener corrección
 
        accion = await loop.run_in_executor(
 
            None, lambda: transcribir_y_corregir(audio_bytes, inv_actual)
 
        )
 
        print(f"🎙️  Acción detectada: {accion}")
 
        # Aplicar corrección al inventario
 
        fue_aplicada = _aplicar_correccion(accion)
 
        print(f"✏️  Corrección aplicada: {fue_aplicada}")
 
        # Decir la respuesta después de procesar
 
        decir_correccion(accion)
 
        ultima_correccion = accion
 
        inv   = list(inventario_acumulado.values())
 
        total = sum(p["cantidad"] for p in inv)
 
        # Broadcast con corrección
 
        await ws_manager.broadcast({
 
            "tipo":       "correccion",
 
            "accion":     accion,
 
            "inventario": inv,
 
            "total":      total
 
        })
 
        return {"status": "ok", "accion": accion, "aplicada": fue_aplicada}
 
    except Exception as e:
 
        await ws_manager.broadcast({"tipo": "error_voz", "mensaje": str(e)})
 
        return JSONResponse({"error": str(e)}, status_code=500)
 
    finally:
 
        _grabando = False
 
 
@app.get("/corregir_voz_continuo")
 
async def corregir_voz_continuo(segundos: float = 10.0):
 
    """Escucha continuamente correcciones de voz hasta detectar una acción."""
 
    global _grabando, ultima_correccion
 
    if _grabando:
 
        return JSONResponse({"error": "Ya hay una grabación en curso"}, status_code=409)
 
    _grabando = True
 
    try:
 
        inv_actual = list(inventario_acumulado.values())
 
        # Notificar UI que empieza la grabación
 
        await ws_manager.broadcast({"tipo": "grabando", "segundos": segundos})
 
        # Grabar en executor para no bloquear el event loop
 
        loop     = asyncio.get_event_loop()
 
        usb_idx  = encontrar_usb_mic()
 
        audio_bytes = await loop.run_in_executor(
 
            None, lambda: grabar_audio(segundos, usb_idx)
 
        )
 
        # Notificar UI que se está procesando
 
        await ws_manager.broadcast({"tipo": "procesando_voz"})
 
        # Transcribir y obtener corrección
 
        accion = await loop.run_in_executor(
 
            None, lambda: transcribir_y_corregir(audio_bytes, inv_actual)
 
        )
 
        print(f"🎙️  Acción detectada: {accion}")
 
        # Aplicar corrección al inventario
 
        fue_aplicada = _aplicar_correccion(accion)
 
        print(f"✏️  Corrección aplicada: {fue_aplicada}")
 
        # Decir la respuesta después de procesar
 
        decir_correccion(accion)
 
        ultima_correccion = accion
 
        inv   = list(inventario_acumulado.values())
 
        total = sum(p["cantidad"] for p in inv)
 
        # Broadcast con corrección
 
        await ws_manager.broadcast({
 
            "tipo":       "correccion",
 
            "accion":     accion,
 
            "inventario": inv,
 
            "total":      total
 
        })
 
        return {"status": "ok", "accion": accion, "aplicada": fue_aplicada}
 
    except Exception as e:
 
        await ws_manager.broadcast({"tipo": "error_voz", "mensaje": str(e)})
 
        return JSONResponse({"error": str(e)}, status_code=500)
 
    finally:
 
        _grabando = False
 
 
@app.get("/dispositivos_audio")
 
def dispositivos_audio():
 
    return {"dispositivos": listar_dispositivos()}
 
 
@app.get("/inventario")
 
def obtener_inventario():
 
    inv = list(inventario_acumulado.values())
 
    return {"status": "ok", "inventario": inv, "total": sum(p["cantidad"] for p in inv)}
 
 
@app.get("/limpiar")
 
def limpiar_inventario():
 
    global inventario_acumulado, ultima_correccion
 
    inventario_acumulado = {}
 
    ultima_correccion    = None
 
    decir_inventario_limpio()
 
    return {"status": "ok"}
 

@app.get("/set_volume")
def configurar_volumen(volume: int = 100):
    """Configura el volumen de audio (0-200)."""
    set_volume(volume)
    return {"status": "ok", "volumen": volume}

 
@app.get("/ultimo")
 
def ultimo():
 
    return {"status": "ok", "resultado": ultimo_resultado}
 
 
@app.get("/debug")
 
async def debug():
 
    return {"ultimo_resultado": ultimo_resultado, "inventario": list(inventario_acumulado.values())}
 
 
@app.get("/", response_class=HTMLResponse)
 
def interfaz():
 
    with open("templates/index.html") as f:
 
        return f.read()
 
@app.get("/leer_inventario")
 
def leer_inventario():
    inv =list (inventario_acumulado.values)
    decir_inventario(inv)
    return {"estatus": "ok"}