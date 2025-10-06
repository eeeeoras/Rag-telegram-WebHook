# api/index.py

import asyncio
import json
import logging
from http.server import BaseHTTPRequestHandler

# Importamos la instancia de la aplicación y la clase Update desde nuestra lógica principal
# Es crucial que 'application' se cree en handlers.py para que se inicialice una sola vez
# cuando Vercel cargue la función.
from bot_logic.handlers import application, Update

# Configuramos un logger para este archivo también
logger = logging.getLogger(__name__)

class handler(BaseHTTPRequestHandler):
    """
    Esta clase es el manejador de la función serverless de Vercel.
    Hereda de BaseHTTPRequestHandler para procesar peticiones HTTP.
    El nombre 'handler' en minúsculas es una convención que Vercel busca.
    """

    def do_POST(self):
        """
        Este método se ejecuta automáticamente cada vez que Vercel recibe una petición POST
        en esta ruta. Telegram SIEMPRE envía las actualizaciones de los mensajes vía POST.
        """
        try:
            # 1. Leer el cuerpo de la petición enviada por Telegram
            # Obtenemos la longitud del contenido para saber cuánto leer
            content_len = int(self.headers.get('Content-Length', 0))
            # Leemos el cuerpo de la petición, que está en formato de bytes
            post_body_bytes = self.rfile.read(content_len)
            # Convertimos los bytes a un diccionario de Python (JSON)
            update_json = json.loads(post_body_bytes)

            # 2. Convertir el diccionario JSON a un objeto 'Update' de la librería
            # La librería python-telegram-bot tiene una función para esto.
            # Necesita el diccionario y la instancia del bot (que está dentro de 'application')
            update = Update.de_json(update_json, application.bot)

            # 3. Procesar la actualización de forma asíncrona
            # Las funciones de nuestro bot son asíncronas (async def), pero do_POST es síncrona.
            # Necesitamos un "puente" para ejecutar código asíncrono desde un contexto síncrono.
            # asyncio.get_event_loop().run_until_complete(...) hace exactamente eso.
            # application.process_update() es la función mágica que recibe el update
            # y lo envía al handler correcto (CommandHandler, MessageHandler, etc.)
            loop = asyncio.get_event_loop_policy().get_event_loop()
            loop.run_until_complete(application.process_update(update))

            # 4. Enviar una respuesta de éxito (200 OK) a Telegram
            # Esto es CRUCIAL. Si no respondemos, Telegram pensará que nuestro webhook
            # ha fallado y seguirá intentando enviar la misma actualización una y otra vez.
            self.send_response(200)
            self.end_headers()
            # No es necesario escribir un cuerpo en la respuesta.

        except json.JSONDecodeError as e:
            logger.error(f"Error al decodificar el JSON de Telegram: {e}")
            self.send_response(400) # Bad Request
            self.end_headers()
        except Exception as e:
            # Si algo sale mal en nuestro código, lo registramos en los logs de Vercel
            # y enviamos una respuesta de error al servidor.
            logger.error(f"Error crítico al procesar la petición del webhook: {e}", exc_info=True)
            self.send_response(500) # Internal Server Error
            self.end_headers()