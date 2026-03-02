from google import genai

from google.genai import types

import io, json, re

from PIL import Image
 
client = genai.Client(api_key="AIzaSyAM4qYwLsa_qCuuQVoJOV6_YrurW26KkJI")
 
PROMPT = """
Analiza esta imagen y responde ÚNICAMENTE con JSON válido, sin texto extra, sin markdown.
 
SOLO detecta estos tipos de productos:
- Frutas (manzana, naranja, plátano, mango, fresa, etc.)
- Verduras (zanahoria, lechuga, tomate, brócoli, cebolla, etc.)
- Envases de alimentos (leche, yogurt, jugo, refresco, agua, etc.)
 
IGNORA COMPLETAMENTE: personas, manos, mesas, sillas, fondos, ropa, electrodomésticos, y cualquier otro objeto que NO sea fruta, verdura o envase de alimento.
 
Si detectas productos válidos:
{
  "detectados": [
    {
      "nombre": "nombre del producto en español",
      "marca": "marca si la identificas, o null",
      "categoria": "fruta | verdura | lacteo | bebida | otro",
      "estado": "bueno | maduro | verde | podrido | desconocido",
      "cantidad": 1,
      "descripcion": "descripción corta"
    }
  ],
  "total": 0,
  "resumen": "descripción corta de lo detectado"
}
 
Si NO hay frutas, verduras ni envases de alimentos visibles:
{
  "detectados": [],
  "total": 0,
  "resumen": "No se detectaron frutas, verduras ni envases"
}
"""
 
def identificar_productos(imagen: Image.Image) -> dict:

    try:

        imagen = imagen.resize((640, 480))

        buffer = io.BytesIO()

        imagen.save(buffer, format="JPEG", quality=90)

        buffer.seek(0)
 
        respuesta = client.models.generate_content(

            model="gemini-2.5-flash",

            contents=[

                PROMPT,

                types.Part.from_bytes(

                    data=buffer.read(),

                    mime_type="image/jpeg"

                )

            ]

        )
 
        texto = respuesta.text.strip()

        texto = texto.replace("```json", "").replace("```", "").strip()

        print(f"✅ Respuesta Gemini: {texto}")
 
        match = re.search(r'\{.*\}', texto, re.DOTALL)

        if match:

            data = json.loads(match.group())

            # Calcular total real

            data["total"] = sum(p.get("cantidad", 1) for p in data.get("detectados", []))

            return data
 
        return {"detectados": [], "total": 0, "resumen": "No se pudo parsear la respuesta"}
 
    except Exception as e:

        print(f"❌ Error: {str(e)}")

        return {"detectados": [], "total": 0, "resumen": f"Error: {str(e)}"}
 