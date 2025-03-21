import os
import time
from typing import List, Dict
from huggingface_hub import HfApi, hf_hub_download

class ModelManager:
    #FIXME: В данный момент папка с моделями создается там, где запускается бэкенд, надо исправить это недоразумение
    MODELS_DIR = "./models/"
    CACHE_TIME = 1800  # 30 минут кеширования
    api = HfApi()
    cache = {"models": [], "last_update": 0}

    def __init__(self):
        self.ensure_models_dir()

    def ensure_models_dir(self):
        os.makedirs(self.MODELS_DIR, exist_ok=True)

    def get_available_models(self) -> List[Dict]:
        current_time = time.time()
        if self.cache["models"] and (current_time - self.cache["last_update"] < self.CACHE_TIME):
            return self.cache["models"]  # Если кеш свежий, возвращаем его

        try:
            models = self.api.list_models(filter="gguf", limit=20)  # Запрашиваем ТОЛЬКО GGUF модели
            available_models = [
                {
                    "name": model.id,
                    "repo_id": model.id,
                    "file_name": self.get_gguf_filename(model.id),
                }
                for model in models
            ]

            self.cache["models"] = available_models
            self.cache["last_update"] = current_time  # Обновляем кеш
            return available_models
        except Exception as e:
            print(f"Ошибка при получении моделей: {e}")
            return []

    def get_gguf_filename(self, repo_id: str) -> str:
        try:
            files = self.api.list_repo_files(repo_id)
            for file in files:
                if file.endswith(".gguf"):
                    return file
        except Exception as e:
            print(f"Ошибка при поиске файлов в {repo_id}: {e}")
        return ""

    def get_model_path(self, model_name: str) -> str:
        available_models = self.get_available_models()
        model_info = next((m for m in available_models if m["name"] == model_name), None)

        if not model_info:
            raise ValueError(f"Модель {model_name} не найдена на Hugging Face.")

        local_path = os.path.join(self.MODELS_DIR, model_info["file_name"])  # Используем глобальную переменную
        return local_path


    def download_model(self, model_name: str) -> str: # Функция для загрузки модели
        available_models = self.get_available_models()
        model_info = next((m for m in available_models if m["name"] == model_name), None)

        if not model_info:
            raise ValueError(f"Модель {model_name} не найдена на Hugging Face.")
        
        if not model_info["file_name"]:
            raise ValueError(f"У модели {model_name} нет GGUF-файла.")
        
        local_path = os.path.join(self.MODELS_DIR, model_info["file_name"])
        
        if os.path.exists(local_path):
            print(f"Модель {model_name} уже загружена.")
            return local_path
        
        print(f"Скачивание модели {model_name}...")
        try:
            hf_hub_download(repo_id=model_info["repo_id"], filename=model_info["file_name"], local_dir=self.MODELS_DIR)
            print(f"Модель {model_name} успешно загружена.")
            return local_path
        except Exception as e:
            print(f"Ошибка при загрузке модели {model_name}: {e}")
            raise