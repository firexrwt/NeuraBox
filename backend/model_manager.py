import os
import time
import platformdirs
from typing import List, Dict
from huggingface_hub import HfApi, hf_hub_download, ModelInfo
import re

APP_NAME = "NeuraBox"
APP_AUTHOR = "NeuraBoxTeam"

USER_DATA_DIR = platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)


class ModelManager:
    MODELS_DIR = os.path.join(USER_DATA_DIR, "models")
    CACHE_TIME = 1800

    def __init__(self, hf_token: str | None = None):
        self.hf_token = hf_token
        self.api = HfApi(token=self.hf_token) if self.hf_token else HfApi()
        self.cache: Dict = {"models": [], "last_update": 0}
        self.ensure_models_dir()

    def ensure_models_dir(self):
        try:
            os.makedirs(self.MODELS_DIR, exist_ok=True)
        except OSError as e:
            print(f"Ошибка создания папки моделей {self.MODELS_DIR}: {e}")
            raise

    def get_model_path(self, model_name: str) -> str | None:
        available_models = self.get_available_models()
        model_info = next((m for m in available_models if
                           m.get("file_name") == model_name or m.get("repo_id") == model_name or m.get(
                               "name") == model_name), None)

        if not model_info:
            local_path_direct = os.path.join(self.MODELS_DIR, model_name)
            if os.path.exists(local_path_direct) and model_name.endswith(".gguf"):
                print(f"Модель {model_name} найдена напрямую в {self.MODELS_DIR}.")
                return local_path_direct
            print(f"Модель {model_name} не найдена в кеше или локально.")
            return None

        file_name_from_info = model_info.get("file_name")
        if not file_name_from_info:
            print(f"Ошибка: Отсутствует 'file_name' в информации для модели {model_name}.")
            return None

        local_path = os.path.join(self.MODELS_DIR, file_name_from_info)
        if os.path.exists(local_path):
            return local_path
        else:
            print(f"Файл {file_name_from_info} для модели {model_name} не найден в {self.MODELS_DIR} (не установлен).")
            return None

    def get_available_models(self) -> List[Dict]:
        current_time = time.time()
        models_output: List[Dict] = []

        if self.cache["models"] and (current_time - self.cache["last_update"] < self.CACHE_TIME):
            print(f"Возвращаем кешированные модели ({len(self.cache['models'])} шт.)")
            for model in self.cache["models"]:
                if model.get("file_name"):
                    local_path = os.path.join(self.MODELS_DIR, model["file_name"])
                    model["installed"] = os.path.exists(local_path)
            return self.cache["models"]

        print("Обновление списка моделей...")
        added_identifiers = set()

        try:
            if os.path.isdir(self.MODELS_DIR):
                for file in os.listdir(self.MODELS_DIR):
                    if file.endswith(".gguf"):
                        if file not in added_identifiers:
                            metadata = self.get_model_metadata(file)
                            models_output.append({
                                "name": file,
                                "repo_id": None,
                                "file_name": file,
                                "installed": True,
                                **metadata
                            })
                            added_identifiers.add(file)
            else:
                print(f"Папка моделей {self.MODELS_DIR} не найдена. Пропуск локальных моделей.")
        except Exception as e:
            print(f"Ошибка чтения локальных моделей из {self.MODELS_DIR}: {e}")

        try:
            hf_models_iterator = self.api.list_models(filter="gguf", sort="downloads", direction=-1, limit=50)
            print("Запрос моделей с Hugging Face...")
            count = 0
            for model in hf_models_iterator:
                count += 1
                if model.id in added_identifiers:
                    continue
                try:
                    print(f"Обработка HF модели: {model.id}")
                    model_info: ModelInfo = self.api.model_info(model.id)
                    file_name = self.get_gguf_filename(model.id)
                    if file_name:
                        if file_name not in added_identifiers:
                            metadata = self.get_hf_model_metadata(model_info, file_name)
                            local_path = os.path.join(self.MODELS_DIR, file_name)
                            models_output.append({
                                "name": model.id,
                                "repo_id": model.id,
                                "file_name": file_name,
                                "installed": os.path.exists(local_path),
                                **metadata
                            })
                            added_identifiers.add(model.id)
                            added_identifiers.add(file_name)
                        else:
                            print(f"Модель с файлом {file_name} ({model.id}) уже добавлена (вероятно, локально).")
                    else:
                        print(f"Не найден GGUF файл для {model.id}")
                except Exception as e:
                    print(f"Ошибка обработки репозитория {model.id}: {e}")
                    continue
            print(f"Обработано {count} моделей с Hugging Face.")
        except Exception as e:
            print(f"Критическая ошибка при получении списка моделей из Hugging Face: {e}")
            if not models_output:
                print("Не удалось получить модели ни локально, ни с HF.")
                return []

        self.cache["models"] = models_output
        self.cache["last_update"] = current_time
        print(f"Список моделей обновлен. Всего: {len(models_output)} моделей.")
        return models_output

    def get_gguf_filename(self, repo_id: str) -> str | None:
        try:
            files = self.api.list_repo_files(repo_id, repo_type="model")
            gguf_files = [f for f in files if f.endswith(".gguf")]
            if not gguf_files: return None
            preferred_quants = ["Q5_K_M", "Q4_K_M", "Q8_0"]
            for quant in preferred_quants:
                for f in gguf_files:
                    if quant in f.upper(): return f
            return gguf_files[0]
        except Exception as e:
            print(f"Ошибка при поиске файлов в {repo_id}: {e}")
            return None

    def get_file_size(self, file_name: str) -> str:
        local_path = os.path.join(self.MODELS_DIR, file_name)
        if os.path.exists(local_path):
            try:
                size_bytes = os.path.getsize(local_path)
                if size_bytes > 1024 * 1024 * 1024:
                    return f"{size_bytes / (1024 ** 3):.2f} GB"
                elif size_bytes >= 1024 * 1024:
                    return f"{size_bytes / (1024 ** 2):.1f} MB"
                elif size_bytes >= 1024:
                    return f"{size_bytes / 1024:.1f} KB"
                else:
                    return f"{size_bytes} B"
            except Exception as e:
                print(f"Ошибка получения размера локального файла {local_path}: {e}")
                return "Error"
        else:
            return "Not Found"

    def get_model_metadata(self, file_name: str) -> Dict:
        param_match = re.search(r"(\d+(\.\d+)?[Bb])", file_name)
        parameters = param_match.group(1).upper() if param_match else "?"

        type_guess = "Text"
        lower_name = file_name.lower()
        if "vision" in lower_name or "image" in lower_name:
            type_guess = "Vision"
        elif "instruct" in lower_name or "chat" in lower_name:
            type_guess = "Instruct"
        elif "video" in lower_name:
            type_guess = "Video"
        elif "multimodal" in lower_name:
            type_guess = "Multimodal"

        size = self.get_file_size(file_name)

        return {"parameters": parameters, "type": type_guess, "size": size}

    def get_hf_model_metadata(self, model_info: ModelInfo, file_name: str) -> Dict:
        parameters = "?"
        try:
            if model_info.cardData and isinstance(model_info.cardData, dict):
                if model_info.cardData.get('model_metadata', {}).get('inference', {}).get('parameters', {}).get(
                        'count'):
                    count = model_info.cardData['model_metadata']['inference']['parameters']['count']
                    if count > 1_000_000_000:
                        parameters = f"{count / 1_000_000_000:.1f}B"
                    elif count > 1_000_000:
                        parameters = f"{count / 1_000_000:.0f}M"
                elif model_info.cardData.get('model-index', [{}])[0].get('results', [{}])[0].get('model_details',
                                                                                                 {}).get('Parameters'):
                    params_str = model_info.cardData['model-index'][0]['results'][0]['model_details']['Parameters']
                    param_match = re.search(r"(\d+(\.\d+)?)\s*(B|M)", str(params_str), re.IGNORECASE)
                    if param_match: parameters = param_match.group(1) + param_match.group(3).upper()
            elif parameters == "?" and model_info.config:
                if model_info.config.get("num_parameters"):
                    count = model_info.config["num_parameters"]
                    if count > 1_000_000_000:
                        parameters = f"{count / 1_000_000_000:.1f}B"
                    elif count > 1_000_000:
                        parameters = f"{count / 1_000_000:.0f}M"
                elif model_info.config.get("hidden_size") and model_info.config.get("num_hidden_layers"):
                    pass
            elif parameters == "?":
                param_match = re.search(r"(\d+(\.\d+)?[Bb])", model_info.id)
                if param_match: parameters = param_match.group(1).upper()
        except Exception as e:
            print(f"Ошибка извлечения параметров для {model_info.id}: {e}")
            parameters = "?"

        model_type = "Text"
        try:
            if model_info.pipeline_tag:
                pipeline = model_info.pipeline_tag.lower()
                if "text-generation" in pipeline or "conversational" in pipeline:
                    model_type = "Instruct" if "instruct" in model_info.id.lower() or "chat" in model_info.id.lower() else "Text"
                elif "text-classification" in pipeline or "fill-mask" in pipeline or "summarization" in pipeline:
                    model_type = "Text"
                elif "image" in pipeline or "vision" in pipeline or "depth-estimation" in pipeline:
                    model_type = "Vision"
                elif "audio" in pipeline:
                    model_type = "Audio"
                elif "video" in pipeline:
                    model_type = "Video"
                elif "multimodal" in pipeline:
                    model_type = "Multimodal"
            elif "vision" in model_info.id.lower() or "image" in model_info.id.lower():
                model_type = "Vision"
            elif "instruct" in model_info.id.lower() or "chat" in model_info.id.lower():
                model_type = "Instruct"
            elif "video" in model_info.id.lower():
                model_type = "Video"
            elif "multimodal" in model_info.id.lower():
                model_type = "Multimodal"
        except Exception as e:
            print(f"Ошибка определения типа для {model_info.id}: {e}")
            model_type = "?"

        size = "N/A"
        try:
            if model_info.siblings:
                target_file_info = next((f for f in model_info.siblings if f.rfilename == file_name), None)
                if target_file_info and target_file_info.size is not None:
                    size_bytes = target_file_info.size
                    if size_bytes > 1024 * 1024 * 1024:
                        size = f"{size_bytes / (1024 ** 3):.2f} GB"
                    elif size_bytes >= 1024 * 1024:
                        size = f"{size_bytes / (1024 ** 2):.1f} MB"
                    elif size_bytes >= 1024:
                        size = f"{size_bytes / 1024:.1f} KB"
                    elif size_bytes == 0:
                        size = "0 B"
                    else:
                        size = f"{size_bytes} B"
                else:
                    if target_file_info is None:
                        print(f"Файл {file_name} не найден в siblings для {model_info.id}")
                    elif target_file_info.size is None:
                        print(f"Атрибут size is None для файла {file_name} в {model_info.id}")
            else:
                print(f"Нет информации о файлах (siblings) для {model_info.id}")
        except Exception as e:
            print(f"Ошибка получения размера файла {file_name} для {model_info.id} из model_info: {e}")
            size = "Error"

        return {"parameters": parameters, "type": model_type, "size": size}

    def download_model(self, model_repo_id: str) -> str:
        try:
            model_info: ModelInfo = self.api.model_info(model_repo_id)
            file_name = self.get_gguf_filename(model_repo_id)
            if not file_name: raise ValueError(f"Не найден GGUF файл в репозитории {model_repo_id}.")
            local_path = os.path.join(self.MODELS_DIR, file_name)
            if os.path.exists(local_path):
                print(f"Модель {model_repo_id} (файл {file_name}) уже загружена в {self.MODELS_DIR}.")
                for model in self.cache["models"]:
                    if model.get("repo_id") == model_repo_id or model.get("file_name") == file_name: model[
                        "installed"] = True
                return local_path
            print(f"Скачивание файла {file_name} из репозитория {model_repo_id} в {self.MODELS_DIR}...")
            self.ensure_models_dir()
            downloaded_path = hf_hub_download(repo_id=model_repo_id, filename=file_name, local_dir=self.MODELS_DIR,
                                              local_dir_use_symlinks=False)
            if downloaded_path != local_path: print(
                f"Предупреждение: Файл скачан в {downloaded_path}, ожидался {local_path}")
            print(f"Модель {model_repo_id} (файл {file_name}) успешно загружена в {downloaded_path}.")
            self.cache["last_update"] = 0
            return downloaded_path
        except Exception as e:
            print(f"Ошибка при загрузке модели {model_repo_id}: {e}")
            file_name = None
            try:
                if 'model_info' in locals() and hasattr(model_info, 'id'):
                    file_name = self.get_gguf_filename(model_info.id)
                potential_incomplete_path = os.path.join(self.MODELS_DIR,
                                                         file_name + ".incomplete") if file_name else None
                if potential_incomplete_path and os.path.exists(potential_incomplete_path):
                    os.remove(potential_incomplete_path);
                    print(f"Удален частично скачанный файл: {potential_incomplete_path}")
            except Exception as remove_err:
                print(f"Ошибка при удалении неполного файла: {remove_err}")
            raise

    def delete_model(self, file_name_to_delete: str) -> bool:
        local_path = os.path.join(self.MODELS_DIR, file_name_to_delete)
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
                print(f"Файл модели {file_name_to_delete} удален.")
                for model in self.cache["models"]:
                    if model.get("file_name") == file_name_to_delete: model["installed"] = False; break
                return True
            except OSError as e:
                print(f"Ошибка при удалении файла {local_path}: {e}")
                return False
        else:
            print(f"Файл {file_name_to_delete} не найден для удаления.")
            for model in self.cache["models"]:
                if model.get("file_name") == file_name_to_delete: model["installed"] = False; break
            return False