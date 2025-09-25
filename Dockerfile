# Базовый образ Python
FROM python:3.11-slim AS base

# Устанавливаем зависимости системы
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Устанавливаем зависимости Python отдельно для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Создаем папки для загрузок и статических файлов
RUN mkdir -p /app/download /app/static \
    && adduser --disabled-password --gecos '' appuser \
    && chown -R appuser /app

# Переходим под непривилегированного пользователя
USER appuser

# Открываем порт
EXPOSE 8000

# Команда запуска приложения
CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "8000"]
