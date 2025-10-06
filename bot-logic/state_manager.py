# bot_logic/state_manager.py

import json
import os
import logging

# Obtenemos una instancia del logger para este módulo
logger = logging.getLogger(__name__)

# Vercel (y la mayoría de las plataformas serverless) solo garantiza
# el acceso de escritura al directorio /tmp. Usaremos este directorio
# para almacenar los archivos de sesión de cada usuario.
STATE_DIR = '/tmp/bot_states'

# Nos aseguramos de que el directorio de estados exista al iniciar el bot.
# Si ya existe, exist_ok=True previene un error.
try:
    os.makedirs(STATE_DIR, exist_ok=True)
except OSError as e:
    logger.error(f"CRÍTICO: No se pudo crear el directorio de estado en {STATE_DIR}: {e}")

def get_state_filepath(user_id: int) -> str:
    """
    Construye la ruta completa al archivo de estado para un usuario específico.
    Ejemplo: /tmp/bot_states/state_123456789.json
    """
    return os.path.join(STATE_DIR, f'state_{user_id}.json')

def load_state(user_id: int) -> dict:
    """
    Carga el estado de un usuario desde su archivo JSON.
    Devuelve un diccionario vacío si el archivo no existe o está corrupto.
    """
    filepath = get_state_filepath(user_id)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            # Si el archivo está vacío o malformado, lo tratamos como un estado nuevo.
            logger.warning(f"El archivo de estado para el usuario {user_id} estaba corrupto o vacío. Se reiniciará el estado.")
            return {}
        except Exception as e:
            logger.error(f"Error inesperado al cargar el estado para el usuario {user_id}: {e}")
            return {}
    # Si el archivo no existe, es la primera interacción del usuario en esta sesión.
    return {}

def save_state(user_id: int, state: dict):
    """
    Guarda el estado de un usuario (un diccionario de Python) en su archivo JSON.
    Sobrescribe el archivo si ya existe.
    """
    filepath = get_state_filepath(user_id)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"CRÍTICO: No se pudo guardar el estado para el usuario {user_id} en {filepath}: {e}")