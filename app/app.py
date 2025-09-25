from fastapi import FastAPI, APIRouter, Query, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from .utils import download_audio
import os
import asyncio
import uuid
from typing import Optional, Dict, Any
import json
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube Audio Downloader API",
    description="API для скачивания аудио с YouTube и других платформ",
    version="1.0.0"
)

# Константы
DOWNLOAD_FOLDER = "download"
STATIC_FOLDER = "static"
MAX_FILE_SIZE_MB = 50
SUPPORTED_PLATFORMS = ["youtube", "youtu.be"]
COMING_SOON_PLATFORMS = ["vk.com", "soundcloud.com", "spotify.com"]

# Создание папок
for folder in [DOWNLOAD_FOLDER, STATIC_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)
        logger.info(f"Создана папка: {folder}")

# Подключение статических файлов
app.mount("/static", StaticFiles(directory=STATIC_FOLDER), name="static")

# Словарь для отслеживания активных подключений
active_connections: Dict[str, WebSocket] = {}

# Создаём роутер с префиксом /api
api_router = APIRouter(prefix="/api", tags=["API"])

class DownloadManager:
    """Менеджер для управления загрузками"""
    
    def __init__(self):
        self.downloads: Dict[str, Dict[str, Any]] = {}
    
    def create_download_task(self, url: str, maxsize: int) -> str:
        task_id = str(uuid.uuid4())
        self.downloads[task_id] = {
            "url": url,
            "maxsize": maxsize,
            "status": "created",
            "progress": 0,
            "created_at": datetime.now().isoformat(),
            "mp3_path": None,
            "thumb_path": None,
            "error": None
        }
        return task_id
    
    def update_download(self, task_id: str, **kwargs):
        if task_id in self.downloads:
            self.downloads[task_id].update(kwargs)
            self.downloads[task_id]["updated_at"] = datetime.now().isoformat()
    
    def get_download(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.downloads.get(task_id)
    
    def cleanup_old_downloads(self):
        # Очистка старых загрузок (можно добавить логику по времени)
        pass

download_manager = DownloadManager()

def is_supported_platform(url: str) -> bool:
    """Проверяет, поддерживается ли платформа"""
    return any(platform in url.lower() for platform in SUPPORTED_PLATFORMS)

def get_platform_from_url(url: str) -> str:
    """Определяет платформу по URL"""
    for platform in SUPPORTED_PLATFORMS:
        if platform in url.lower():
            return "YouTube"
    
    for platform in COMING_SOON_PLATFORMS:
        if platform in url.lower():
            return platform.upper()
    
    return "Неизвестная платформа"

@app.websocket("/ws/download")
async def websocket_download(ws: WebSocket, url: str, maxsize: int = 15):
    """WebSocket эндпоинт для скачивания с прогрессом"""
    connection_id = str(uuid.uuid4())
    
    try:
        await ws.accept()
        active_connections[connection_id] = ws
        logger.info(f"WebSocket подключение установлено: {connection_id}")
        
        # Валидация входных данных
        if not url or not url.strip():
            await ws.send_json({"error": "URL не может быть пустым"})
            return
        
        if maxsize <= 0 or maxsize > MAX_FILE_SIZE_MB:
            await ws.send_json({"error": f"Размер файла должен быть от 1 до {MAX_FILE_SIZE_MB} МБ"})
            return
        
        # Проверка поддерживаемой платформы
        platform = get_platform_from_url(url)
        if not is_supported_platform(url):
            if any(coming_soon in url.lower() for coming_soon in COMING_SOON_PLATFORMS):
                await ws.send_json({
                    "error": f"Поддержка {platform} скоро будет добавлена! 🚀",
                    "coming_soon": True,
                    "platform": platform
                })
            else:
                await ws.send_json({"error": "Неподдерживаемая платформа. Поддерживается только YouTube."})
            return
        
        # Создаем задачу загрузки
        task_id = download_manager.create_download_task(url, maxsize)
        
        await ws.send_json({
            "status": f"Начинаем загрузку с {platform}...",
            "progress": 0,
            "task_id": task_id
        })
        
        # Запускаем загрузку
        mp3_path, thumb_path = await download_audio(
            url=url, 
            ws=ws, 
            out_folder=DOWNLOAD_FOLDER, 
            max_filesize_mb=maxsize
        )
        
        if mp3_path and os.path.exists(mp3_path):
            # Обновляем информацию о загрузке
            download_manager.update_download(
                task_id,
                status="completed",
                progress=100,
                mp3_path=mp3_path,
                thumb_path=thumb_path
            )
            
            # Отправляем ссылки на скачивание
            response_data = {
                "message": "Загрузка завершена успешно! 🎵",
                "download_url": f"/api/file/{os.path.basename(mp3_path)}",
                "task_id": task_id,
                "file_size_mb": round(os.path.getsize(mp3_path) / (1024 * 1024), 2)
            }
            
            if thumb_path and os.path.exists(thumb_path):
                response_data["thumbnail_url"] = f"/api/file/{os.path.basename(thumb_path)}"
            
            await ws.send_json(response_data)
            
        else:
            download_manager.update_download(
                task_id,
                status="failed",
                error=f"Не удалось скачать файл или он превышает {maxsize} МБ"
            )
            await ws.send_json({
                "error": f"Не удалось скачать файл или он превышает {maxsize} МБ",
                "task_id": task_id
            })
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket отключен: {connection_id}")
    except Exception as e:
        logger.error(f"Ошибка в WebSocket: {e}")
        try:
            await ws.send_json({"error": f"Произошла ошибка: {str(e)}"})
        except:
            pass
    finally:
        # Очистка подключения
        if connection_id in active_connections:
            del active_connections[connection_id]
        try:
            await ws.close()
        except:
            pass

@api_router.get("/platforms")
async def get_supported_platforms():
    """Получить список поддерживаемых платформ"""
    return {
        "supported": [
            {
                "name": "YouTube",
                "domains": ["youtube.com", "youtu.be"],
                "status": "active",
                "features": ["MP3 download", "Thumbnail download", "Progress tracking"]
            }
        ],
        "coming_soon": [
            {
                "name": "VK Music",
                "domains": ["vk.com"],
                "status": "coming_soon",
                "estimated_release": "Q2 2025",
                "features": ["Playlist download", "High quality audio"]
            },
            {
                "name": "SoundCloud",
                "domains": ["soundcloud.com"],
                "status": "coming_soon", 
                "estimated_release": "Q3 2025",
                "features": ["Track download", "Playlist support"]
            },
            {
                "name": "Spotify",
                "domains": ["spotify.com"],
                "status": "planned",
                "estimated_release": "Q4 2025",
                "features": ["Preview download only (due to licensing)"]
            }
        ]
    }

@api_router.get("/download/status/{task_id}")
async def get_download_status(task_id: str):
    """Получить статус загрузки по ID задачи"""
    download = download_manager.get_download(task_id)
    if not download:
        raise HTTPException(status_code=404, detail="Задача загрузки не найдена")
    
    return download

@api_router.post("/download/url")
async def api_download_url(data: dict):
    """API эндпоинт для скачивания (без WebSocket)"""
    url = data.get("url")
    maxsize = data.get("maxsize", 15)
    
    if not url:
        raise HTTPException(status_code=400, detail="URL обязателен")
    
    if not is_supported_platform(url):
        platform = get_platform_from_url(url)
        if any(coming_soon in url.lower() for coming_soon in COMING_SOON_PLATFORMS):
            return {
                "error": f"Поддержка {platform} скоро будет добавлена! 🚀",
                "coming_soon": True,
                "platform": platform
            }
        else:
            raise HTTPException(status_code=400, detail="Неподдерживаемая платформа")
    
    # Создаем mock WebSocket для совместимости
    class MockWebSocket:
        async def send_json(self, data):
            pass
    
    mock_ws = MockWebSocket()
    
    try:
        mp3_path, thumb_path = await download_audio(
            url=url,
            ws=mock_ws,
            out_folder=DOWNLOAD_FOLDER,
            max_filesize_mb=maxsize
        )
        
        if mp3_path and os.path.exists(mp3_path):
            response_data = {
                "message": "Файл готов",
                "download_url": f"/api/file/{os.path.basename(mp3_path)}",
                "file_size_mb": round(os.path.getsize(mp3_path) / (1024 * 1024), 2)
            }
            
            if thumb_path and os.path.exists(thumb_path):
                response_data["thumbnail_url"] = f"/api/file/{os.path.basename(thumb_path)}"
            
            return response_data
        else:
            raise HTTPException(status_code=400, detail=f"Файл слишком большой или произошла ошибка. Максимум {maxsize} МБ.")
            
    except Exception as e:
        logger.error(f"Ошибка при скачивании: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/file/{file_name}")
async def serve_file(file_name: str):
    """Отдача скачанных файлов"""
    # Проверка безопасности имени файла
    if ".." in file_name or "/" in file_name or "\\" in file_name:
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден")
    
    # Определяем тип файла для правильного Content-Type
    file_ext = os.path.splitext(file_name)[1].lower()
    media_type = "application/octet-stream"
    
    if file_ext == ".mp3":
        media_type = "audio/mpeg"
    elif file_ext in [".jpg", ".jpeg"]:
        media_type = "image/jpeg"
    elif file_ext == ".png":
        media_type = "image/png"
    elif file_ext == ".webp":
        media_type = "image/webp"
    
    return FileResponse(
        path=file_path, 
        filename=file_name,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"}  # Кэширование на час
    )

@api_router.get("/stats")
async def get_stats():
    """Получить статистику приложения"""
    download_files = [f for f in os.listdir(DOWNLOAD_FOLDER) if f.endswith('.mp3')]
    total_size_mb = sum(
        os.path.getsize(os.path.join(DOWNLOAD_FOLDER, f)) 
        for f in download_files
    ) / (1024 * 1024)
    
    return {
        "total_downloads": len(download_files),
        "total_size_mb": round(total_size_mb, 2),
        "active_connections": len(active_connections),
        "supported_platforms": len(SUPPORTED_PLATFORMS),
        "coming_soon_platforms": len(COMING_SOON_PLATFORMS)
    }

@api_router.delete("/cleanup")
async def cleanup_old_files():
    """Очистка старых файлов (только для администраторов)"""
    try:
        files_deleted = 0
        for filename in os.listdir(DOWNLOAD_FOLDER):
            file_path = os.path.join(DOWNLOAD_FOLDER, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                files_deleted += 1
        
        return {"message": f"Удалено {files_deleted} файлов"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Подключаем роутер к приложению
app.include_router(api_router)

@app.get("/", response_class=HTMLResponse)
async def home():
    """Главная страница"""
    html_path = os.path.join(STATIC_FOLDER, "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    else:
        # Если файл не найден, возвращаем простую HTML страницу
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>YouTube Audio Downloader</title>
        </head>
        <body>
            <h1>YouTube Audio Downloader</h1>
            <p>Поместите файл index.html в папку static/</p>
            <p>API документация доступна по адресу <a href="/docs">/docs</a></p>
        </body>
        </html>
        """)

@app.get("/health")
async def health_check():
    """Проверка здоровья приложения"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

# Событие запуска приложения
@app.on_event("startup")
async def startup_event():
    logger.info("YouTube Audio Downloader API запущен")
    logger.info(f"Папка загрузок: {DOWNLOAD_FOLDER}")
    logger.info(f"Статические файлы: {STATIC_FOLDER}")

# Событие остановки приложения  
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("YouTube Audio Downloader API остановлен")
    
    # Закрываем все активные WebSocket соединения
    for connection_id, ws in active_connections.items():
        try:
            await ws.close()
        except:
            pass
    
    active_connections.clear()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)