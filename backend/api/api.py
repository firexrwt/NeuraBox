import os
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.model_manager import ModelManager
from llama_cpp import Llama

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создаём API-роутер
router = APIRouter()

# Менеджер моделей и глобальная переменная для текущей модели
model_manager = ModelManager()
llm_instance = None

# Классы для валидации запросов
class ModelRequestBody(BaseModel):
    model: str  # Название модели для загрузки

class QueryRequestBody(BaseModel):
    text: str  # Текст запроса
    model: str  # Выбранная модель
    use_internet: bool = False  # Использовать ли веб-поиск (пока заглушка)
    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 0.9


# Получение списка доступных моделей
@router.get("/models")
async def list_available_models():
    # Получает список всех доступных моделей (локально и на Hugging Face).
    try:
        models = model_manager.get_available_models()
        for model in models:
            local_path = os.path.join(model_manager.MODELS_DIR, model["file_name"])
            model["installed"] = os.path.exists(local_path)
        return models
    except Exception as e:
        logger.error(f"Ошибка при получении списка моделей: {str(e)}")
        raise HTTPException(status_code=500, detail="Ошибка при получении моделей.")

# Установка модели (скачивание)
@router.post("/install_model")
async def install_model(request: ModelRequestBody):
    # Скачивает модель, если её ещё нет локально.
    try:
        local_path = model_manager.download_model(request.model)
        return {"message": f"Модель {request.model} установлена", "local_path": local_path}
    except Exception as e:
        logger.error(f"Ошибка установки модели: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка установки модели: {str(e)}")

# Переключение на другую модель
def load_model(model_name: str):
    # Загружает модель LLaMA в память
    global llm_instance
    try:
        model_path = model_manager.get_model_path(model_name)
        logger.info(f"Загружаем модель: {model_name}")
        llm_instance = Llama(model_path=model_path, n_ctx=2048, n_threads=8)
    except Exception as e:
        logger.error(f"Ошибка загрузки модели: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки модели: {str(e)}")


# Запрос в модель
@router.post("/query")
async def process_query(request: QueryRequestBody):
    # Обрабатывает запрос пользователя и заставляет модель отвечать на том же языке.
    global llm_instance

    logger.info(f"Получен запрос: {request}")
    logger.info(f"Выбранная модель: {request.model}")

    try:
        # Проверяем, загружена ли модель
        if not llm_instance or llm_instance.model_path != model_manager.get_model_path(request.model):
            logger.info(f"Модель {request.model} не загружена. Загружаем...")
            load_model(request.model)
            logger.info(f"Модель {request.model} загружена!")

        # Текст запроса пользователя
        user_text = request.text.strip()

        # Улучшенная инструкция для модели
        lang_instruction = """You must answer in the same language as the user's question.
Do not repeat the question. Answer in a complete sentence with useful information."""

        # Новый промпт для модели
        prompt = f"""You are a helpful AI assistant.
{lang_instruction}

User: {user_text}
Assistant:"""

        logger.info(f"Отправляем промпт в модель:\n{prompt}")

        # Отправляем запрос в LLaMA
        response = llm_instance(
            prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            echo=False,  # Не повторять вопрос пользователя
            stop=["\n\n", "User:", "Assistant:"]  # Остановить генерацию при этих токенах
        )

        # Получаем ответ модели
        model_response = response["choices"][0]["text"].strip()
        logger.info(f"Ответ модели: {model_response}")

        if not model_response:
            model_response = "I couldn't generate a response."

        return {
            "response": model_response,
            "model": request.model,
            "tokens_used": response["usage"]["total_tokens"]
        }

    except Exception as e:
        logger.error(f"Ошибка обработки запроса: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
