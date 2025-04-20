# backend.spec (Упрощенная версия для llama-server архитектуры)

# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['backend/main.py'], # Главный скрипт FastAPI
    pathex=['.'],        # Искать модули в корне проекта
    binaries=[],         # Библиотеки НЕ НУЖНЫ здесь
    datas=[],            # Добавьте сюда НЕ-python файлы, если они нужны (кроме .py)
    hiddenimports=[
        # Основные скрытые импорты для FastAPI/Uvicorn
        'uvicorn.lifespan.on',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        # Можно добавить 'httpx' или его зависимости, если будут ошибки ModuleNotFound при запуске backend.exe
        # 'httpx', 'httpcore', 'h11', 'anyio', 'sniffio', 'certifi',
        # Явный импорт модулей вашего API
        'backend.api.api',
        'backend.model_manager',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'unittest', 'llama_cpp', 'ctransformers', 'gguf', 'torch', 'tensorflow'], # Исключаем точно ненужное
    noarchive=False,
    optimize=0
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [], # a.binaries - пусто
    a.datas,
    name='backend',
    debug=False,
    console=True, # Оставляем консоль для логов
)