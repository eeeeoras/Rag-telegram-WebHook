# bot_logic/handlers.py

import os
import logging
import re
import math
import google.generativeai as genai
import ebooklib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest
from ebooklib import epub
import PyPDF2
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import docx
from google.api_core.exceptions import PermissionDenied, InvalidArgument

# Importamos nuestro nuevo gestor de estado
from bot_logic import state_manager

# ===================== CONFIGURACIÓN Y LOGGING =====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_API_KEY_1 = os.getenv("GOOGLE_API_KEY")
GOOGLE_API_KEY_2 = os.getenv("GOOGLE_API_KEY_2")
api_keys = [key for key in [GOOGLE_API_KEY_1, GOOGLE_API_KEY_2] if key]

if not TELEGRAM_TOKEN: raise ValueError("⚠️ TELEGRAM_TOKEN no encontrado.")
if not api_keys: raise ValueError("⚠️ No se encontró ninguna GOOGLE_API_KEY.")

logger.info(f"✅ Se encontraron {len(api_keys)} claves API de Google para utilizar.")

preloaded_library = {}
BOOKS_DIR = "books"
BOOKS_PER_PAGE = 5
MAX_CHARS = 4000000
TELEGRAM_MSG_LIMIT = 4096

# ===================== FUNCIONES DE EXTRACCIÓN DE TEXTO (Completas) =====================
def epub_to_text(file_path):
    try:
        book = epub.read_epub(file_path)
        text_parts = [BeautifulSoup(item.get_content(), "html.parser").get_text(separator=" ", strip=True) for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)]
        return "\n\n".join(text_parts)
    except Exception as e:
        logger.error(f"Error procesando EPUB '{file_path}': {e}")
        return None

def pdf_to_text(file_path):
    text = ""
    try:
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                if (extracted := page.extract_text()): text += extracted + "\n"
        return text
    except Exception as e:
        logger.error(f"Error procesando PDF '{file_path}': {e}")
        return None

def txt_to_text(file_path):
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f: return f.read()
    except Exception as e:
        logger.error(f"Error leyendo TXT '{file_path}': {e}")
        return None

def html_to_text(file_path):
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f: return BeautifulSoup(f.read(), "html.parser").get_text(separator=" ", strip=True)
    except Exception as e:
        logger.error(f"Error procesando HTML '{file_path}': {e}")
        return None

def docx_to_text(file_path):
    try:
        doc = docx.Document(file_path)
        full_text = [para.text for para in doc.paragraphs]
        return '\n'.join(full_text)
    except Exception as e:
        logger.error(f"Error procesando DOCX '{file_path}': {e}")
        return None

def scan_books_directory():
    logger.info(f"Escaneando biblioteca en: '{BOOKS_DIR}'...")
    if not os.path.exists(BOOKS_DIR):
        logger.warning(f"El directorio '{BOOKS_DIR}' no fue encontrado. Creándolo.")
        os.makedirs(BOOKS_DIR)
        return
    preloaded_library.clear()
    valid_extensions = {".pdf", ".epub", ".txt", ".html", ".docx"}
    for category_name in sorted(os.listdir(BOOKS_DIR)):
        category_path = os.path.join(BOOKS_DIR, category_name)
        if os.path.isdir(category_path):
            books_in_category = [filename for filename in sorted(os.listdir(category_path)) if os.path.splitext(filename.lower())[1] in valid_extensions]
            if books_in_category: preloaded_library[category_name] = books_in_category
    if not preloaded_library:
        logger.info("No se encontraron categorías con libros válidos.")
    else:
        logger.info(f"Biblioteca local cargada con {len(preloaded_library)} categorías.")

# ===================== FUNCIONES DE AYUDA =====================
async def send_final_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup = None):
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except BadRequest as e:
        if "Can't parse entities" in str(e):
            logger.warning("Fallo en parseo de Markdown. Reenviando como texto plano.")
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        else: raise e

def _build_paginated_book_list(category_name: str, page: int):
    books_in_category = preloaded_library.get(category_name, [])
    if not books_in_category: return "⚠️ Esta categoría está vacía o ya no existe.", None
    total_pages = math.ceil(len(books_in_category) / BOOKS_PER_PAGE)
    start_index = page * BOOKS_PER_PAGE; end_index = start_index + BOOKS_PER_PAGE
    keyboard = []
    for i in range(start_index, end_index):
        if i < len(books_in_category):
            filename = books_in_category[i]
            keyboard.append([InlineKeyboardButton(f"📖 {filename}", callback_data=f"select_{category_name}_{i}")])
    pagination_row = []
    if page > 0: pagination_row.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"page_{category_name}_{page - 1}"))
    pagination_row.append(InlineKeyboardButton(f"Pág {page + 1}/{total_pages}", callback_data="noop"))
    if end_index < len(books_in_category): pagination_row.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"page_{category_name}_{page + 1}"))
    keyboard.append(pagination_row)
    keyboard.append([InlineKeyboardButton("⬅️ Volver a Categorías", callback_data="back_to_categories")])
    message_text = f"📖 *Libros en '{category_name}':*\n\nSelecciona un libro para cargarlo."
    return message_text, InlineKeyboardMarkup(keyboard)

# ===================== HANDLERS DE TELEGRAM (ADAPTADOS PARA STATE_MANAGER) =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    welcome_message = f"""
👋 ¡Bienvenido *{user_name}* a tu Asistente de Estudio Personal! 📚

Soy *M-AIc*, tu bot diseñado para hacer tu *aprendizaje más fácil e inteligente*. Simplemente sube tus materiales de lectura o selecciona uno de nuestra base de datos con el comando "/books" y estaré listo para responder a cualquier pregunta que tengas sobre ellos.

📂 *Formatos que Acepto:*
· *PDF* 📄
· *DOCX* (Microsoft Word) 📝
· *EPUB* (Libros electrónicos) 📱
· *TXT* (Archivos de texto plano) 📜
· *HTML* (Páginas web guardadas) 🌐

🚀 *¿Cómo Funciona?*

1.  *Sube un archivo* en cualquiera de los formatos de la lista anterior.
2.  *Haz tu pregunta*, puedes preguntar lo que quieras pero solo relacionado con el contenido del archivo.
3.  *¡Obtén la respuesta!* Te responderé usando *únicamente* la información contenida en el documento que subiste.

¡Empecemos a estudiar! 🧠✨
"""
    await update.message.reply_text(text=welcome_message, parse_mode=ParseMode.MARKDOWN)

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    document = update.message.document
    if not document:
        await update.message.reply_text("⚠️ No se ha podido obtener el archivo.")
        return
    
    # En Vercel, los archivos temporales se guardan en /tmp
    file_path = f"/tmp/{document.file_id}_{document.file_name}"
    
    processing_message = await update.message.reply_text("⏳ Procesando tu archivo...")
    
    try:
        file = await document.get_file()
        await file.download_to_drive(custom_path=file_path)
        
        # Guardamos la RUTA del archivo en el estado del usuario
        state = state_manager.load_state(user_id)
        state['current_book_path'] = file_path
        state_manager.save_state(user_id, state)
        
        await processing_message.edit_text(f"✅ Archivo '{document.file_name}' cargado. ¡Ya puedes preguntar!")
        logger.info(f"Archivo '{document.file_name}' guardado para {user_id} en {file_path}")
    except Exception as e:
        logger.error(f"Error en handle_file: {e}", exc_info=True)
        await processing_message.edit_text("⚠️ Error inesperado al procesar tu archivo.")

async def show_categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    scan_books_directory()
    if not preloaded_library:
        await update.message.reply_text("No hay libros en la biblioteca local.")
        return
    keyboard = [[InlineKeyboardButton(f"📁 {cat_name}", callback_data=f"cat_{cat_name}")] for cat_name in preloaded_library.keys()]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "📚 *Categorías Disponibles:*\n\nSelecciona una categoría."
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_name = query.data.split('cat_', 1)[1]
    message_text, reply_markup = _build_paginated_book_list(category_name, page=0)
    await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def handle_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, category_name, page_str = query.data.split('_', 2)
        page = int(page_str)
    except (ValueError, IndexError):
        await query.edit_message_text("⚠️ Error de paginación.")
        return
    message_text, reply_markup = _build_paginated_book_list(category_name, page)
    await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def handle_book_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        _, category_name, book_index_str = query.data.split('_', 2)
        book_index = int(book_index_str)
        filename = preloaded_library[category_name][book_index]
    except (ValueError, KeyError, IndexError):
        await query.edit_message_text("⚠️ Selección inválida.")
        return
    
    # Guardamos la RUTA del libro precargado en el estado del usuario
    file_path = os.path.join(BOOKS_DIR, category_name, filename)
    state = state_manager.load_state(user_id)
    state['current_book_path'] = file_path
    state_manager.save_state(user_id, state)
    
    logger.info(f"Usuario {user_id} seleccionó: '{filename}'.")
    await query.edit_message_text(f"✅ Libro '{filename}' seleccionado. ¡Ya puedes preguntar!")

async def ask_question_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = state_manager.load_state(user_id)
    
    if 'current_book_path' not in state or not os.path.exists(state['current_book_path']):
        await update.message.reply_text("⚠️ Primero debes subir o seleccionar un archivo.")
        return
        
    question = update.message.text
    state['last_question'] = question
    state_manager.save_state(user_id, state)
    
    logger.info(f"Usuario {user_id} preguntó: '{question}'")
    keyboard = [[InlineKeyboardButton("🎯 Simple", callback_data="detail_simple"), InlineKeyboardButton("📚 Detallada", callback_data="detail_detailed")]]
    await update.message.reply_text('¿Cómo prefieres la respuesta?', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_detail_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    state = state_manager.load_state(user_id)
    question = state.get('last_question')
    detail_level = query.data.split('_')[1]

    if not question:
        await query.edit_message_text("⚠️ No he podido recuperar tu pregunta.")
        return
        
    target_message = None
    try:
        await query.edit_message_text("🧠 Analizando tu pregunta...", reply_markup=None)
        target_message = query.message
    except BadRequest as e:
        if "not found" in str(e).lower():
            logger.warning("Condición de carrera. Creando nuevo mensaje.")
            target_message = await context.bot.send_message(chat_id=user_id, text="🧠 Analizando tu pregunta...")
        else: raise e

    if target_message:
        logger.info(f"Usuario {user_id} eligió detalle '{detail_level}' para: '{question}'")
        await _generate_and_send_answer(target_message, user_id, question, detail_level, context)

async def handle_suggested_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    state = state_manager.load_state(user_id)
    suggestions_map = state.get('suggestions', {})
    question = suggestions_map.get(query.data)

    if not question:
        await query.edit_message_text("⚠️ Este botón ha expirado.")
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest as e:
        logger.warning(f"No se pudieron quitar los botones: {e}")

    state['last_question'] = question
    state_manager.save_state(user_id, state)
    
    logger.info(f"Usuario {user_id} eligió pregunta: '{question}'")
    keyboard = [[InlineKeyboardButton("🎯 Simple", callback_data="detail_simple"), InlineKeyboardButton("📚 Detallada", callback_data="detail_detailed")]]
    await query.message.reply_text(f"Nueva pregunta:\n*\"{question}\"*\n\n¿Cómo prefieres la respuesta?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def _generate_and_send_answer(target_message, user_id, question, detail_level, context):
    state = state_manager.load_state(user_id)
    book_path = state.get('current_book_path')
    
    if not book_path or not os.path.exists(book_path):
        await target_message.edit_text("⚠️ No encuentro el libro cargado. Por favor, súbelo o selecciónalo de nuevo.")
        return
        
    # Leemos el texto del libro desde el archivo en cada pregunta
    ext = os.path.splitext(book_path.lower())[1]
    processors = {".pdf": pdf_to_text, ".epub": epub_to_text, ".txt": txt_to_text, ".html": html_to_text, ".docx": docx_to_text}
    book_text = processors[ext](book_path)

    if not book_text:
        await target_message.edit_text("⚠️ No se pudo leer el contenido del libro seleccionado.")
        return

    detail_instructions = {"simple": "explica de forma muy concisa, en uno o dos párrafos.", "detailed": """Es crucial que la respuesta sea profunda y exhaustiva. Busca en el texto múltiples puntos de vista, ejemplos, definiciones y contexto relacionado para construir tu respuesta. La respuesta no debe ser un simple resumen; debe tener varios párrafos y explorar el tema a fondo, utilizando toda la información relevante disponible en el documento."""}
    
    prompt = f"""
    Eres un tutor experto del documento proporcionado. Tu misión es responder a las preguntas del usuario basándote ESTRICTAMENTE en esa información brinda la informacion estructurada de una forma visual por encabezados y usando emojis.
    **Regla de Detalle:** El usuario ha pedido una respuesta '{detail_level}'. Debes {detail_instructions[detail_level]}
    **Regla de Contenido:** Si la pregunta no se puede responder con el documento, responde amablemente que no encuentras la información.
    **Regla de Formato OBLIGATORIA:** Tu respuesta DEBE seguir esta estructura exacta:
    1.  La respuesta a la pregunta del usuario.
    2.  El separador especial `###PREGUNTAS_SUGERIDAS###`. Esta sección NO es opcional.
    3.  Una lista de 2 o 3 preguntas de seguimiento relevantes, cada una en una nueva línea.
    **Ejemplo de Salida:**
    La dermis es la capa de la piel situada bajo la epidermis. Se compone principalmente de tejido conectivo y protege al cuerpo del estrés y la tensión.
    ###PREGUNTAS_SUGERIDAS###
    ¿Cuáles son las subcapas de la dermis?
    ¿Qué función tienen los fibroblastos?
    --- INICIO DEL CONTENIDO DEL DOCUMENTO ---
    {book_text}
    --- FIN DEL CONTENIDO DEL DOCUMENTO ---
    **Pregunta del usuario:** {question}
    **Tu respuesta estructurada:**
    """
    
    response = None
    last_error = None
    
    for i, api_key in enumerate(api_keys):
        try:
            logger.info(f"Intentando llamada a la API con la clave #{i+1}...")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = model.generate_content(prompt)
            _ = response.text 
            logger.info(f"✅ Éxito con la clave API #{i+1}.")
            break
        except (PermissionDenied, InvalidArgument) as e:
            logger.warning(f"La clave API #{i+1} falló: {e}")
            last_error = e
            continue
        except Exception as e:
            logger.error(f"Error al generar contenido con la clave #{i+1}: {e}", exc_info=False)
            last_error = e
            break 

    try:
        if response is None:
            raise Exception("No se pudo obtener una respuesta válida de la API.", last_error)

        full_text = response.text
        logger.debug("Respuesta completa de la IA: %s", full_text)
        
        main_answer, suggested_questions = full_text, []
        if "###PREGUNTAS_SUGERIDAS###" in full_text:
            parts = full_text.split("###PREGUNTAS_SUGERIDAS###")
            main_answer = parts[0].strip()
            suggested_questions = [q.strip() for q in parts[1].strip().split('\n') if q.strip() and len(q) > 1]
        
        keyboard = []
        if suggested_questions:
            suggestions_map = {}
            for i, q in enumerate(suggested_questions):
                callback_id = f"sugg_{i}"
                suggestions_map[callback_id] = q
                button_text = q[:60] + '...' if len(q) > 60 else q
                keyboard.append([InlineKeyboardButton(f"› {button_text}", callback_data=callback_id)])
            
            # Guardamos las sugerencias en el estado del usuario
            state['suggestions'] = suggestions_map
            state_manager.save_state(user_id, state)
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        try:
            await target_message.delete()
        except BadRequest as e:
            if "not found" in str(e).lower(): logger.warning("El mensaje 'Analizando...' ya había sido borrado.")
            else: raise e

        if len(main_answer) > TELEGRAM_MSG_LIMIT:
            parts = [main_answer[i:i + TELEGRAM_MSG_LIMIT] for i in range(0, len(main_answer), TELEGRAM_MSG_LIMIT)]
            for i, part in enumerate(parts):
                final_markup = reply_markup if i == len(parts) - 1 else None
                await send_final_message(context, user_id, part, final_markup)
        else:
            await send_final_message(context, user_id, main_answer, reply_markup)
            
    except Exception as e:
        logger.error(f"Error al procesar la respuesta para {user_id}: {e}", exc_info=True)
        try:
            await target_message.edit_text("⚠️ Lo siento, ocurrió un error con la IA.")
        except BadRequest:
            logger.warning("No se pudo editar el mensaje de error porque ya no existía.")

# ===================== FUNCIÓN DE CONFIGURACIÓN DE LA APLICACIÓN =====================
def setup_application():
    """Crea la instancia de la aplicación y registra todos los handlers."""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    application.add_handler(CommandHandler("books", show_categories_command))
    application.add_handler(CallbackQueryHandler(show_categories_command, pattern=r'^back_to_categories$'))
    application.add_handler(CallbackQueryHandler(handle_pagination, pattern=r'^page_'))
    application.add_handler(CallbackQueryHandler(handle_category_selection, pattern=r'^cat_'))
    application.add_handler(CallbackQueryHandler(handle_book_selection, pattern=r'^select_'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ask_question_handler))
    application.add_handler(CallbackQueryHandler(handle_detail_choice, pattern=r'^detail_'))
    application.add_handler(CallbackQueryHandler(handle_suggested_question, pattern=r'^sugg_'))
    application.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern=r'^noop$'))
    
    return application

# Inicializar la app una vez al arrancar el servidor (para Vercel)
scan_books_directory()
application = setup_application()