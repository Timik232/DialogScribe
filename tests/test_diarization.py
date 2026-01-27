#!/usr/bin/env python3
"""
Тестовый скрипт для проверки исправлений в диаризации.
"""

import os
import sys
from pathlib import Path

# Добавляем путь к пакету
sys.path.insert(0, str(Path(__file__).parent))

def test_pipeline_loading():
    """Тест загрузки pipeline с правильными параметрами."""
    print("Тест загрузки pipeline диаризации...")
    
    try:
        from gigaam_transcriber.diarization import DiarizationManager
        
        manager = DiarizationManager()
        pipeline = manager.pipeline
        
        print("✅ Pipeline загружен успешно!")
        print(f"   Тип: {type(pipeline)}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Тест исправлений диаризации")
    print("=" * 60)
    print()
    
    success = test_pipeline_loading()
    
    print()
    print("=" * 60)
    if success:
        print("✅ Тест пройден!")
    else:
        print("❌ Тест не пройден")
    print("=" * 60)
    
    sys.exit(0 if success else 1)
