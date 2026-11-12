#!/bin/bash
# Скрипт для быстрой остановки BMI30.200.py

echo "🛑 Останавливаю BMI30.200.py..."

# Сначала пытаемся мягко завершить
pkill -f BMI30.200.py 2>/dev/null
KILLED=$?

if [ $KILLED -eq 0 ]; then
    echo "   Отправлен сигнал завершения (SIGTERM)..."
    sleep 1
    
    # Проверяем, завершился ли процесс
    if pgrep -f BMI30.200.py > /dev/null; then
        echo "   Процесс не завершился, применяю принудительную остановку..."
        pkill -9 -f BMI30.200.py
        sleep 0.5
    fi
fi

# Финальная проверка
if pgrep -f BMI30.200.py > /dev/null; then
    echo "❌ Не удалось остановить процесс!"
    echo "   Активные процессы:"
    pgrep -af BMI30.200.py
    exit 1
else
    echo "✅ Процесс BMI30.200.py успешно остановлен"
    exit 0
fi
