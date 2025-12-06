import os
import logging
import GPUtil
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Dict, Optional, List
import uuid
from dotenv import load_dotenv
import sqlite3
import datetime
import platformdirs
import httpx

from model_manager import ModelManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

APP_NAME = "NeuraBox"
APP_AUTHOR = "NeuraBoxTeam"
USER_DATA_DIR = platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)
try:
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    logger.info(f"Используется папка данных пользователя: {USER_DATA_DIR}")
except OSError as e:
    logger.error(f"Не удалось создать папку данных пользователя {USER_DATA_DIR}: {e}")
    raise
ENV_PATH = os.path.join(USER_DATA_DIR, ".env")
DATABASE_PATH = os.path.join(USER_DATA_DIR, "neurabox_chats.db")
logger.info(f"Ожидаемый путь к .env файлу: {ENV_PATH}")
logger.info(f"Ожидаемый путь к базе данных: {DATABASE_PATH}")
dotenv_loaded = load_dotenv(dotenv_path=ENV_PATH)
if dotenv_loaded:
    logger.info(f".env файл успешно загружен из {ENV_PATH}")
else:
    logger.info(f".env файл не найден или пуст по пути {ENV_PATH}")


def init_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("""
                       CREATE TABLE IF NOT EXISTS chats
                       (
                           chat_id
                           TEXT
                           PRIMARY
                           KEY,
                           title
                           TEXT
                           NOT
                           NULL,
                           model_used
                           TEXT,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP,
                           last_modified_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       );
                       """)
        cursor.execute("""
                       CREATE TABLE IF NOT EXISTS messages
                       (
                           message_id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           chat_id
                           TEXT
                           NOT
                           NULL,
                           sender
                           TEXT
                           NOT
                           NULL
                           CHECK (
                           sender
                           IN
                       (
                           'user',
                           'ai'
                       )),
                           content TEXT NOT NULL,
                           timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           FOREIGN KEY
                       (
                           chat_id
                       ) REFERENCES chats
                       (
                           chat_id
                       ) ON DELETE CASCADE
                           );
                       """)
        cursor.execute("""
                       CREATE TRIGGER IF NOT EXISTS update_chat_modtime
            AFTER INSERT ON messages
            FOR EACH ROW
                       BEGIN
                       UPDATE chats
                       SET last_modified_at = CURRENT_TIMESTAMP
                       WHERE chat_id = NEW.chat_id;
                       END;
                       """)
        conn.commit()
        logger.info(f"База данных инициализирована: {DATABASE_PATH}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка инициализации БД ({DATABASE_PATH}): {e}")
        raise
    finally:
        if conn: conn.close()


init_db()


def get_db_connection():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к БД ({DATABASE_PATH}): {e}")
        raise HTTPException(status_code=500, detail="Ошибка подключения к базе данных.")


def db_add_chat(chat_id: str, title: str, model_used: Optional[str] = None):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chats (chat_id, title, model_used, created_at, last_modified_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, title, model_used, datetime.datetime.now(), datetime.datetime.now())
        )
        conn.commit()
        logger.info(f"Чат '{title}' (ID: {chat_id}) добавлен в БД.")
    except sqlite3.IntegrityError:
        logger.warning(f"Чат с ID {chat_id} уже существует.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка добавления чата {chat_id} в БД: {e}")
        conn.rollback()
        raise HTTPException(status_code=500, detail="Ошибка сохранения чата в БД.")
    finally:
        if conn: conn.close()


def db_add_message(chat_id: str, sender: str, content: str):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO messages (chat_id, sender, content) VALUES (?, ?, ?)", (chat_id, sender, content))
        cursor.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ? AND sender = 'user'", (chat_id,))
        user_message_count = cursor.fetchone()[0]
        if user_message_count == 1 and sender == 'user':
            cursor.execute("UPDATE chats SET title = ? WHERE chat_id = ? AND title LIKE 'New Chat %'",
                           (content[:50], chat_id))
            logger.info(f"Название чата {chat_id} обновлено на: {content[:50]}")
        conn.commit()
        logger.info(f"Сообщение от '{sender}' добавлено в чат {chat_id}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка добавления сообщения в чат {chat_id}: {e}")
        conn.rollback()
        raise HTTPException(status_code=500, detail="Ошибка сохранения сообщения в БД.")
    finally:
        if conn: conn.close()


def db_get_chats() -> List[Dict]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, title, model_used, last_modified_at FROM chats ORDER BY last_modified_at DESC")
        chats = [dict(row) for row in cursor.fetchall()]
        for chat in chats:
            if isinstance(chat.get('last_modified_at'), str):
                chat['last_modified_at'] = datetime.datetime.fromisoformat(chat['last_modified_at'])
        return chats
    except sqlite3.Error as e:
        logger.error(f"Ошибка получения чатов из БД: {e}")
        raise HTTPException(status_code=500, detail="Ошибка чтения списка чатов.")
    finally:
        if conn: conn.close()


def db_get_messages(chat_id: str) -> List[Dict]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,))
    chat_exists = cursor.fetchone()
    if not chat_exists:
        conn.close()
        logger.warning(f"Попытка получить сообщения для несуществующего чата: {chat_id}")
        return None
    try:
        cursor.execute(
            "SELECT message_id, sender, content, timestamp FROM messages WHERE chat_id = ? ORDER BY timestamp ASC",
            (chat_id,))
        messages_data = [dict(row) for row in cursor.fetchall()]
        for msg in messages_data:
            if isinstance(msg.get('timestamp'), str):
                msg['timestamp'] = datetime.datetime.fromisoformat(msg['timestamp'])
        return messages_data
    except sqlite3.Error as e:
        logger.error(f"Ошибка получения сообщений для чата {chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка чтения сообщений чата.")
    finally:
        if conn: conn.close()


def db_delete_chat(chat_id: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        deleted_rows = cursor.rowcount
        conn.commit()
        if deleted_rows > 0:
            logger.info(f"Чат {chat_id} и его сообщения удалены из БД.")
            return True
        else:
            logger.warning(f"Попытка удаления несуществующего чата {chat_id}.")
            return False
    except sqlite3.Error as e:
        logger.error(f"Ошибка удаления чата {chat_id} из БД: {e}")
        conn.rollback()
        raise HTTPException(status_code=500, detail="Ошибка удаления чата.")
    finally:
        if conn: conn.close()


router = APIRouter()

HF_TOKEN = os.getenv("HF_TOKEN")
if not HF_TOKEN:
    logger.warning("HF_TOKEN не задан в .env или окружении.")

model_manager = None

global_model_settings = {
    "max_tokens": 1024,
    "temperature": 0.7,
    "top_p": 0.95
}


class TokenRequestBody(BaseModel):
    token: str


class ModelRequestBody(BaseModel):
    model: str


class ModelSettingsRequestBody(BaseModel):
    max_tokens: int = Field(default=global_model_settings["max_tokens"], ge=1, le=8192)
    temperature: float = Field(default=global_model_settings["temperature"], ge=0.0, le=2.0)
    top_p: float = Field(default=global_model_settings["top_p"], ge=0.0, le=1.0)


class QueryRequestBody(ModelSettingsRequestBody):
    text: str
    model: str
    chat_id: str
    use_internet: bool = False


def get_gpu_layers():
    try:
        gpus = GPUtil.getGPUs()
        if not gpus: logger.info("GPU не найдено."); return 0
        gpu0 = gpus[0]
        total_mem = gpu0.memoryTotal
        logger.info(f"Найден GPU: {gpu0.name}, память: {total_mem / 1024:.1f} GB")
        if total_mem >= 22000:
            return -1
        elif total_mem >= 15000:
            return 40
        elif total_mem >= 10000:
            return 30
        elif total_mem >= 7000:
            return 20
        elif total_mem >= 5000:
            return 15
        else:
            return 10
    except Exception as e:
        logger.warning(f"Ошибка при определении GPU: {e}");
        return 0


@router.get("/models")
async def list_available_models(request: Request):
    global model_manager
    hf_token = request.headers.get("X-HF-Token", HF_TOKEN)
    if model_manager is None or model_manager.hf_token != hf_token:
        logger.info(f"Инициализация ModelManager с токеном {'(есть)' if hf_token else '(нет)'}")
        try:
            model_manager = ModelManager(hf_token=hf_token)
        except Exception as e:
            logger.error(f"Ошибка инициализации ModelManager: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка инициализации менеджера моделей: {e}")
    try:
        return model_manager.get_available_models()
    except Exception as e:
        logger.error(f"Ошибка при получении списка моделей: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при получении списка моделей.")


@router.post("/install_model")
async def install_model(request: ModelRequestBody):
    if model_manager is None:
        raise HTTPException(status_code=400,
                            detail="Менеджер моделей не инициализирован. Сначала выполните GET /models.")
    try:
        logger.info(f"Начало установки модели: {request.model}")
        local_path = model_manager.download_model(request.model)
        logger.info(f"Модель {request.model} успешно установлена в: {local_path}")
        return {"message": f"Модель {request.model} установлена", "local_path": local_path}
    except Exception as e:
        logger.error(f"Ошибка установки модели {request.model}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка установки модели: {str(e)}")


@router.post("/update_model_settings")
async def update_model_settings(request: ModelSettingsRequestBody):
    global global_model_settings
    try:
        settings_changed = False
        # Используем Pydantic V2+ model_dump или V1 dict()
        try:
            update_data = request.model_dump(exclude_unset=True)
        except AttributeError:  # Fallback for Pydantic V1
            update_data = request.dict(exclude_unset=True)

        for key, value in update_data.items():
            if key in global_model_settings and global_model_settings[key] != value:
                global_model_settings[key] = value
                settings_changed = True

        if settings_changed:
            logger.info(f"Глобальные настройки генерации обновлены: {global_model_settings}")
            return {"message": "Настройки модели обновлены", "settings": global_model_settings}
        else:
            logger.info("Настройки генерации не изменились.")
            return {"message": "Настройки модели не изменились", "settings": global_model_settings}
    except Exception as e:
        logger.error(f"Ошибка обновления настроек: {e}")
        raise HTTPException(status_code=500, detail="Ошибка обновления настроек.")


@router.post("/query")
async def process_query(request: QueryRequestBody):
    global global_model_settings

    logger.info(f"Запрос к /query для chat_id: {request.chat_id}, модель (ожидается на сервере): {request.model}")

    user_text = request.text.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Текст запроса не может быть пустым.")
    try:
        db_add_message(request.chat_id, 'user', user_text)
    except HTTPException as db_exc:
        logger.error(f"Не удалось сохранить сообщение пользователя для чата {request.chat_id}. Запрос прерван.")
        raise db_exc
    except Exception as e:
        logger.exception(f"Неожиданная ошибка сохранения сообщения пользователя: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при сохранении запроса.")

    try:
        messages_from_db = db_get_messages(request.chat_id)
        if messages_from_db is None:
            logger.error(f"Чат {request.chat_id} не найден при попытке сформировать историю.")
            raise HTTPException(status_code=404, detail=f"Чат с ID {request.chat_id} не найден.")

        history_limit_pairs = 15
        relevant_history = messages_from_db[-(history_limit_pairs * 2):]

        messages_for_api = []
        system_prompt = """You are NeuraBox, a helpful AI assistant running locally.
        Answer concisely and factually in the same language as the user's last message.
        **Format your response using GitHub Flavored Markdown (GFM).**
        - Use ```python ... ``` for code blocks (replace 'python' with the correct language).
        - Use `inline_code` for inline code.
        - Use **bold** and *italic* text for emphasis.
        - Use lists (`- item` or `1. item`) where appropriate."""
        messages_for_api.append({"role": "system", "content": system_prompt})

        for msg in relevant_history:
            api_role = "user" if msg['sender'] == 'user' else "assistant"
            messages_for_api.append({"role": api_role, "content": msg['content']})

        max_tokens = request.max_tokens if request.max_tokens is not None else global_model_settings["max_tokens"]
        temperature = request.temperature if request.temperature is not None else global_model_settings["temperature"]
        top_p = request.top_p if request.top_p is not None else global_model_settings["top_p"]
        stop_sequences = ["\nUser:", "\nAssistant:", "<|endoftext|>"]

        llama_server_url = "http://127.0.0.1:9016/v1/chat/completions"
        payload = {
            "model": request.model,
            "messages": messages_for_api,
            "temperature": max(0.01, temperature),
            "max_tokens": max_tokens,
            "top_p": top_p,
            "stop": stop_sequences,
        }
        logger.info(f"Отправка запроса к llama-server: {llama_server_url}")

        model_response = None
        tokens_used = 0

        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                response = await client.post(llama_server_url, json=payload)
                logger.info(f"Ответ от llama-server получен: Status={response.status_code}")
                response.raise_for_status()
                api_response_data = response.json()

                if api_response_data.get("choices") and len(api_response_data["choices"]) > 0:
                    message_content = api_response_data["choices"][0].get("message", {}).get("content")
                    if message_content:
                        model_response = message_content.strip()
                usage = api_response_data.get("usage", {})
                tokens_used = usage.get("total_tokens", 0)

            except httpx.RequestError as exc:
                logger.error(f"Ошибка сети при запросе к llama-server: {exc}")
                raise HTTPException(status_code=503, detail=f"Ошибка соединения с сервером модели: {exc}")
            except httpx.HTTPStatusError as exc:
                logger.error(f"Ошибка HTTP от llama-server ({exc.response.status_code}): {exc.response.text}")
                raise HTTPException(status_code=exc.response.status_code,
                                    detail=f"Сервер модели ({exc.response.status_code}): {exc.response.text[:200]}")
            except Exception as exc:
                logger.exception(f"Ошибка обработки ответа от llama-server: {exc}")
                raise HTTPException(status_code=500, detail=f"Ошибка обработки ответа сервера модели: {str(exc)}")

        logger.info(
            f"Ответ модели обработан (Chat ID: {request.chat_id}, токены: {tokens_used}):\n{model_response[:300]}...")

        if not model_response:
            logger.warning(f"Сервер модели вернул пустой ответ для чата {request.chat_id}.")
            model_response = "(Сервер модели не смог сгенерировать ответ)"

        try:
            db_add_message(request.chat_id, 'ai', model_response)
        except HTTPException as db_exc:
            logger.error(f"Не удалось сохранить ответ ИИ для чата {request.chat_id}: {db_exc.detail}")
        except Exception as e:
            logger.exception(f"Неожиданная ошибка сохранения ответа ИИ: {e}")

        return {
            "response": model_response,
            "chat_id": request.chat_id,
            "model": request.model,
            "tokens_used": tokens_used,
            "settings_used": {
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p
            }
        }

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.exception(f"Критическая ошибка обработки /query для чата {request.chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при обработке запроса: {str(e)}")


@router.post("/save_token")
async def save_token(request: TokenRequestBody):
    try:
        current_token = os.getenv("HF_TOKEN")
        new_token = request.token.strip()
        if current_token == new_token: return {"message": "Токен совпадает с текущим"}
        env_file_path = ENV_PATH
        lines = []
        token_found = False
        if os.path.exists(env_file_path):
            try:
                with open(env_file_path, "r", encoding='utf-8') as f:
                    lines = f.readlines()
            except Exception as e:
                logger.error(f"Не удалось прочитать .env файл {env_file_path}: {e}")
        try:
            with open(env_file_path, "w", encoding='utf-8') as f:
                for line in lines:
                    stripped_line = line.strip()
                    if stripped_line and not stripped_line.startswith('#') and stripped_line.startswith("HF_TOKEN="):
                        f.write(f"HF_TOKEN={new_token}\n");
                        token_found = True
                    else:
                        f.write(line)
                if not token_found: f.write(f"\nHF_TOKEN={new_token}\n")
            load_dotenv(dotenv_path=env_file_path, override=True)
            global HF_TOKEN
            HF_TOKEN = os.getenv("HF_TOKEN")
            if model_manager: model_manager.hf_token = HF_TOKEN
            logger.info(f"Токен HF_TOKEN успешно сохранен/обновлен в {env_file_path}")
            return {"message": "Токен успешно сохранен"}
        except Exception as e:
            logger.error(f"Ошибка записи в .env файл {env_file_path}: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка записи токена в файл конфигурации.")
    except Exception as e:
        logger.error(f"Общая ошибка при сохранении токена: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при сохранении токена.")


class ChatInfo(BaseModel):
    chat_id: str
    title: str
    model_used: Optional[str] = None
    last_modified_at: datetime.datetime


class MessageInfo(BaseModel):
    message_id: int
    sender: str
    content: str
    timestamp: datetime.datetime


class ChatCreateResponse(ChatInfo):
    pass


@router.get("/chats", response_model=List[ChatInfo])
async def get_all_chats():
    try:
        chats_data = db_get_chats()
        return chats_data
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.exception(f"Неожиданная ошибка в эндпоинте /chats (GET): {e}")
        raise HTTPException(status_code=500, detail="Не удалось получить список чатов.")


@router.post("/chats", response_model=ChatCreateResponse, status_code=201)
async def create_new_chat():
    try:
        new_chat_id = str(uuid.uuid4())
        initial_title = f"New Chat {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        db_add_chat(chat_id=new_chat_id, title=initial_title)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, title, model_used, last_modified_at FROM chats WHERE chat_id = ?",
                       (new_chat_id,))
        new_chat_data = cursor.fetchone()
        conn.close()
        if new_chat_data:
            chat_dict = dict(new_chat_data)
            if isinstance(chat_dict.get('last_modified_at'), str):
                chat_dict['last_modified_at'] = datetime.datetime.fromisoformat(chat_dict['last_modified_at'])
            return chat_dict
        else:
            logger.error(f"Не удалось найти только что созданный чат {new_chat_id} в БД.")
            raise HTTPException(status_code=500, detail="Ошибка получения данных нового чата.")
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.exception(f"Неожиданная ошибка при создании нового чата: {e}")
        raise HTTPException(status_code=500, detail="Не удалось создать новый чат.")


@router.get("/chats/{chat_id}/messages", response_model=List[MessageInfo])
async def get_chat_messages(chat_id: str):
    try:
        messages_data = db_get_messages(chat_id)
        if messages_data is None:
            raise HTTPException(status_code=404, detail=f"Чат с ID {chat_id} не найден.")
        return messages_data
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.exception(f"Неожиданная ошибка получения сообщений для чата {chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось получить сообщения чата.")


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str):
    try:
        success = db_delete_chat(chat_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Чат с ID {chat_id} не найден.")
        return None
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.exception(f"Неожиданная ошибка при удалении чата {chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось удалить чат.")