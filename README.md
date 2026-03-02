# 🧊 Smart Fridge Inventory

> Inventario inteligente de refrigerador con visión artificial y asistente de voz, corriendo en Raspberry Pi.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat&logo=fastapi&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-2.5_Flash-4285F4?style=flat&logo=google&logoColor=white)
![Raspberry Pi](https://img.shields.io/badge/Raspberry_Pi-4-C51A4A?style=flat&logo=raspberrypi&logoColor=white)

---

## ¿Qué hace?

Una cámara cenital instalada sobre el refrigerador detecta automáticamente qué frutas, verduras y envases entran o salen. El inventario se actualiza en tiempo real en un dashboard web y puede corregirse hablando.

```
Abres el refri → la cámara ve → Gemini identifica → inventario actualizado → notificación por voz
```

---

## Características

- **Detección visual** — Gemini 2.5 Flash analiza cada frame y detecta productos, incluso dentro de bolsas de plástico
- **Diferencia de inventario** — detecta qué se *agregó* y qué se *sacó* entre escaneos
- **Asistente de voz** — di "AI Mabe" para activar correcciones por voz ("agrega 2 manzanas", "elimina la leche")
- **Dashboard en tiempo real** — stream MJPEG + WebSocket, sin recargar la página
- **Respuestas habladas** — gTTS + mpv anuncia los cambios por la bocina USB
- **Detección de movimiento** — solo llama a Gemini cuando hay cambio real en el frame (ahorra peticiones)

---

## Arquitectura

```
├── camera.py          # Captura de frames, detección de movimiento por diferencia de píxeles
├── gemini_service.py  # Análisis de imagen con Gemini API → JSON de productos
├── audio_service.py   # Grabación de audio USB + transcripción y corrección por voz
├── tts_service.py     # Síntesis de voz con gTTS, reproducción con mpv
├── main.py            # API FastAPI, WebSocket, lógica de inventario
└── templates/
    └── index.html     # Dashboard: stream en vivo, inventario, controles
```

---

## Hardware requerido

| Componente | Notas |
|---|---|
| Raspberry Pi 4 (o 3B+) | Mínimo 2 GB RAM recomendado |
| Módulo cámara CSI | Montado de forma cenital sobre el refrigerador |
| Micrófono USB | Probado con Waveshare USB Audio Adapter |
| Bocina USB | Probado con Waveshare USB Audio Adapter |

---

## Instalación

### 1. Dependencias del sistema

```bash
sudo apt update && sudo apt install -y mpv espeak python3-pip
```

### 2. Dependencias Python

```bash
pip install fastapi uvicorn picamera2 pillow numpy google-genai sounddevice gtts --break-system-packages
```

### 3. API Key de Gemini

```bash
export GEMINI_API_KEY="AIza...tu_clave_aqui"
```

> Obtén tu clave gratis en [aistudio.google.com](https://aistudio.google.com)

### 4. Iniciar el servidor

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 5. Abrir el dashboard

```
http://<ip-de-la-raspberry>:8000
```

---

## API REST

| Endpoint | Descripción |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /stream` | Stream MJPEG en vivo |
| `GET /set_puerta?abierta=true` | Activa/desactiva escaneos automáticos |
| `GET /identificar` | Escaneo manual inmediato |
| `GET /corregir?segundos=5` | Graba voz y aplica corrección |
| `GET /inventario` | Inventario acumulado en JSON |
| `GET /limpiar` | Limpia el inventario |
| `GET /set_volume?volume=100` | Ajusta volumen (0–200) |
| `WS  /ws` | WebSocket para actualizaciones en tiempo real |

---

## Parámetros configurables

En `camera.py`:

```python
CAPTURE_W, CAPTURE_H = 640, 480   # Resolución de captura
TARGET_FPS   = 15                  # Límite de FPS
MOTION_EVERY = 3                   # Analizar movimiento cada N frames
DIFF_THRESHOLD = 80                # Sensibilidad por píxel (0–255)
DIFF_RATIO     = 0.10              # % de píxeles que deben cambiar para disparar análisis
_cooldown_secs = 120               # Segundos mínimos entre llamadas a Gemini
```

---

## Flujo de operación

```
1. Usuario abre la puerta → presiona "Abrir" en el dashboard
2. Servidor activa escaneos cada 2 segundos
3. camera.py detecta movimiento por diferencia de píxeles
4. Si hay movimiento → frame enviado a Gemini → JSON con productos detectados
5. Sistema compara con escaneo anterior → detecta agregados y removidos
6. Cambios transmitidos por WebSocket al dashboard + anunciados por voz
7. (Opcional) Usuario dice "AI Mabe" → graba voz → Gemini interpreta corrección → inventario actualizado
8. Usuario cierra la puerta → escaneos se detienen
```

---

## Notas

- El inventario vive en RAM: se pierde al reiniciar el servidor
- Gemini 2.5 Flash tiene **1,500 peticiones/día** en el tier gratuito
- El reconocimiento de voz en el navegador requiere **HTTPS** en producción (funciona en `localhost` sin certificado)
- La palabra clave acepta variantes fonéticas: "AI Mabe", "ay mabe", "maibe"
- En condiciones de luz variable pueden generarse falsos positivos en la detección de movimiento

---

## Stack

`Python` · `FastAPI` · `Picamera2` · `Google Gemini` · `gTTS` · `mpv` · `SoundDevice` · `WebSocket` · `Raspberry Pi`
