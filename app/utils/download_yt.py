import os
import requests
import yt_dlp
import asyncio
import re
from fastapi import WebSocket

async def download_audio(url: str, ws: WebSocket, out_folder: str = "download", max_filesize_mb: int = None):
    """
    Скачивает аудио с YouTube в mp3 и миниатюру с уведомлением прогресса через WebSocket.
    Прогресс отправляется в JSON: {"progress": 0-100, "status": "Загрузка/Конвертация/..."}
    """
    if not os.path.exists(out_folder):
        os.makedirs(out_folder)
    
    loop = asyncio.get_running_loop()
    
    def progress_hook(d):
        try:
            if d['status'] == 'downloading':
                # Получаем процент как число
                percent_str = d.get('_percent_str', '0.0%').replace('%', '').strip()
                try:
                    percent = float(percent_str)
                except (ValueError, TypeError):
                    percent = 0.0
                
                # Отправляем через asyncio
                asyncio.run_coroutine_threadsafe(
                    ws.send_json({"progress": percent, "status": "Загрузка..."}),
                    loop
                )
            elif d['status'] == 'finished':
                asyncio.run_coroutine_threadsafe(
                    ws.send_json({"progress": 95, "status": "Конвертация в mp3..."}),
                    loop
                )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                ws.send_json({"progress": 0, "status": f"Ошибка прогресса: {e}"}),
                loop
            )
    
    # Функция для безопасного имени файла
    def safe_filename(filename):
        # Удаляем небезопасные символы для имени файла
        return re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(out_folder, '%(title)s.%(ext)s'),
        'progress_hooks': [progress_hook],
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }
        ],
        'noplaylist': True,  # Скачиваем только одно видео, не плейлист
    }
    
    # Добавляем ограничение размера файла если указано
    if max_filesize_mb:
        ydl_opts['format'] = f'bestaudio[filesize<{max_filesize_mb}M]/best[filesize<{max_filesize_mb}M]'
    
    try:
        await ws.send_json({"progress": 5, "status": "Получение информации о видео..."})
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Сначала получаем информацию без скачивания
            info = ydl.extract_info(url, download=False)
            title = safe_filename(info.get('title', 'audio'))
            
            await ws.send_json({"progress": 10, "status": f"Начинаем скачивание: {title}"})
            
            # Теперь скачиваем
            info = ydl.extract_info(url, download=True)
            
            # Формируем путь к mp3 файлу
            original_filename = ydl.prepare_filename(info)
            mp3_path = os.path.splitext(original_filename)[0] + '.mp3'
            
            # Проверяем, что файл создался
            if not os.path.exists(mp3_path):
                await ws.send_json({"progress": 0, "status": "Ошибка: mp3 файл не был создан"})
                return None, None
            
            # Проверка размера после скачивания
            if max_filesize_mb:
                file_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
                if file_size_mb > max_filesize_mb:
                    os.remove(mp3_path)
                    await ws.send_json({
                        "progress": 0, 
                        "status": f"Файл ({file_size_mb:.1f} МБ) превышает лимит {max_filesize_mb} МБ и был удален."
                    })
                    return None, None
            
            await ws.send_json({"progress": 90, "status": "Скачивание миниатюры..."})
            
            # Скачать миниатюру
            thumb_path = None
            thumbnails = info.get('thumbnails', [])
            
            if thumbnails:
                # Берем последнюю (обычно самого высокого качества) миниатюру
                thumb_url = thumbnails[-1].get('url')
                
                if thumb_url:
                    try:
                        # Определяем расширение файла
                        thumb_ext = '.jpg'  # по умолчанию
                        if 'webp' in thumb_url.lower():
                            thumb_ext = '.webp'
                        elif 'png' in thumb_url.lower():
                            thumb_ext = '.png'
                        
                        thumb_path = os.path.join(out_folder, f"{title}_thumbnail{thumb_ext}")
                        
                        response = requests.get(thumb_url, timeout=10, stream=True)
                        response.raise_for_status()
                        
                        with open(thumb_path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        
                        await ws.send_json({"progress": 95, "status": "Миниатюра сохранена"})
                    except Exception as e:
                        await ws.send_json({"progress": 95, "status": f"Не удалось скачать обложку: {e}"})
                        thumb_path = None
            else:
                await ws.send_json({"progress": 95, "status": "Миниатюра недоступна"})
        
        await ws.send_json({"progress": 100, "status": "Готово!"})
        return mp3_path, thumb_path
        
    except Exception as e:
        await ws.send_json({"progress": 0, "status": f"Ошибка: {e}"})
        return None, None