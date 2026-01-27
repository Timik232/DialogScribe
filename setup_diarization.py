#!/usr/bin/env python3
"""
Скрипт для настройки доступа к моделям диаризации pyannote.

Помогает проверить и настроить доступ к моделям на HuggingFace.
"""

import os
import sys
from pathlib import Path

# Добавляем путь к пакету
sys.path.insert(0, str(Path(__file__).parent))


def check_hf_token():
    """Проверка наличия HuggingFace токена."""
    token = os.getenv("HF_TOKEN")
    if not token:
        print("❌ HF_TOKEN не установлен!")
        print("\nДля установки токена:")
        print("1. Создайте токен на https://huggingface.co/settings/tokens")
        print("2. Установите переменную окружения:")
        print("   export HF_TOKEN=your_token_here")
        print("   или добавьте в ~/.bashrc")
        return None
    
    print(f"✅ HF_TOKEN установлен (длина: {len(token)} символов)")
    return token


def check_model_access():
    """Проверка доступа к моделям."""
    token = check_hf_token()
    if not token:
        return False
    
    print("\n🔍 Проверка доступа к моделям...")
    
    models_to_check = [
        "pyannote/speaker-diarization-3.1",
        "pyannote/segmentation-3.0",
        "pyannote/speaker-diarization-community-1",
        "pyannote/speaker-diarization",
    ]
    
    try:
        from huggingface_hub import HfApi
        
        api = HfApi(token=token)
        all_accessible = True
        
        for model_id in models_to_check:
            try:
                # Пробуем получить информацию о модели
                model_info = api.model_info(model_id)
                print(f"✅ Доступ к {model_id}: OK")
            except Exception as e:
                error_str = str(e)
                if "403" in error_str or "gated" in error_str.lower():
                    print(f"❌ Доступ к {model_id}: ЗАКРЫТ")
                    print(f"   Перейдите на https://huggingface.co/{model_id}")
                    print(f"   и нажмите 'Agree and access repository'")
                    all_accessible = False
                else:
                    print(f"⚠️  Ошибка при проверке {model_id}: {e}")
                    all_accessible = False
        
        return all_accessible
        
    except ImportError:
        print("⚠️  huggingface_hub не установлен. Установите: pip install huggingface_hub")
        return False
    except Exception as e:
        print(f"❌ Ошибка при проверке доступа: {e}")
        return False


def test_diarization():
    """Тест загрузки модели диаризации."""
    print("\n🧪 Тест загрузки модели диаризации...")
    
    try:
        from gigaam_transcriber import DiarizationManager
        
        manager = DiarizationManager()
        pipeline = manager.pipeline
        
        print("✅ Модель диаризации загружена успешно!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка загрузки модели: {e}")
        return False


def main():
    """Основная функция."""
    print("=" * 60)
    print("Настройка диаризации для GigaAM Transcriber")
    print("=" * 60)
    
    # Проверка токена
    token = check_hf_token()
    if not token:
        return 1
    
    # Проверка доступа
    if not check_model_access():
        print("\n" + "=" * 60)
        print("ИНСТРУКЦИЯ ПО НАСТРОЙКЕ:")
        print("=" * 60)
        print("\n1. Перейдите на следующие страницы и примите условия:")
        print("   - https://huggingface.co/pyannote/speaker-diarization-3.1")
        print("   - https://huggingface.co/pyannote/segmentation-3.0")
        print("   - https://huggingface.co/pyannote/speaker-diarization-community-1")
        print("   - https://huggingface.co/pyannote/speaker-diarization")
        print("\n2. На каждой странице нажмите 'Agree and access repository'")
        print("\n3. Убедитесь, что ваш токен имеет права 'read'")
        print("   (создайте новый токен если нужно: https://huggingface.co/settings/tokens)")
        print("\n4. После принятия условий запустите этот скрипт снова:")
        print("   python setup_diarization.py")
        return 1
    
    # Тест загрузки
    if test_diarization():
        print("\n" + "=" * 60)
        print("✅ Всё настроено! Диаризация готова к использованию.")
        print("=" * 60)
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
