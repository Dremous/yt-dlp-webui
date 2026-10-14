"""yt-dlp WebUI - FastAPI 后端入口。

功能：
- POST /api/download  提交下载任务
- GET  /api/formats   可用输出格式
- GET  /api/tasks     任务列表（前端轮询）
- GET  /api/files     已下载文件列表
- GET  /files/{name}  下载文件
- DELETE /api/files/{name} 删除文件
- POST /api/cookies   导入 Cookie（cookies.txt）
- GET  /api/cookies/status  查询 Cookie 状态
- DELETE /api/cookies 清除 Cookie
- 静态前端挂载在 /
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .downloader import FORMATS, DownloadManager

# ---------------------------------------------------------------------------
# 下载目录与 Cookie 文件（容器内可通过环境变量覆盖）
# ---------------------------------------------------------------------------
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/downloads"))
COOKIE_FILE = os.environ.get("COOKIE_FILE", "/config/cookies.txt")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

manager = DownloadManager(DOWNLOAD_DIR, COOKIE_FILE)

app = FastAPI(title="yt-dlp WebUI", version="1.0.0")


class DownloadRequest(BaseModel):
    url: str = Field(..., description="视频 / 音频 URL")
    format: str = Field("mp4", description="输出格式：mp4 / best / mp3")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/formats")
def get_formats():
    return [{"id": k, "label": v["label"]} for k, v in FORMATS.items()]


@app.post("/api/download")
def start_download(req: DownloadRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请输入合法的 http(s) 链接")
    if req.format not in FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的格式：{req.format}")
    task = manager.submit(url, req.format)
    return {"task_id": task.id}


@app.get("/api/tasks")
def list_tasks():
    return manager.snapshot()


@app.get("/api/files")
def list_files():
    files = []
    for p in sorted(DOWNLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            files.append({
                "name": p.name,
                "size": p.stat().st_size,
                "mtime": int(p.stat().st_mtime),
            })
    return files


@app.get("/files/{filename}")
def get_file(filename: str):
    # 只取纯文件名，杜绝路径穿越
    safe = Path(filename).name
    path = DOWNLOAD_DIR / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, filename=safe)


@app.delete("/api/files/{filename}")
def delete_file(filename: str):
    safe = Path(filename).name
    path = DOWNLOAD_DIR / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    path.unlink()
    return {"ok": True, "deleted": safe}


# ---------------------------------------------------------------------------
# Cookie 导入 / 状态 / 清除
# ---------------------------------------------------------------------------
@app.post("/api/cookies")
async def upload_cookie(file: UploadFile):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Cookie 文件过大（上限 5MB）")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Cookie 文件必须是 UTF-8 文本")
    # 极简校验：Netscape 头 或 JSON 结构，避免乱传
    stripped = text.lstrip()
    if not (stripped.startswith("# Netscape HTTP Cookie File") or stripped.startswith("{")):
        raise HTTPException(status_code=400, detail="无法识别的 Cookie 格式：需为 Netscape cookies.txt 或 JSON")
    manager.save_cookie(content)
    return {"ok": True}


@app.get("/api/cookies/status")
def cookie_status():
    return manager.cookie_info()


@app.delete("/api/cookies")
def delete_cookie():
    manager.remove_cookie()
    return {"ok": True, "deleted": True}


# ---------------------------------------------------------------------------
# 静态前端
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
