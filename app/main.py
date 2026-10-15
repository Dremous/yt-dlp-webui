"""yt-dlp WebUI - FastAPI 后端入口。

功能：
- POST /api/download  提交下载任务
- GET  /api/formats   可用输出格式
- GET  /api/tasks     任务列表（前端轮询）
- GET  /api/files     已下载文件列表
- GET  /files/{filename:path}  下载文件（相对路径，含任务子目录）
- DELETE /api/files/{filename:path} 删除文件
- POST /api/cookies   导入 Cookie（cookies.txt）
- GET  /api/cookies/status  查询 Cookie 状态
- DELETE /api/cookies 清除 Cookie
- 静态前端挂载在 /
"""

import json
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


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    task = manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in ("finished", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail="任务已结束，无法取消")
    # queued：线程 acquire 后自查返回；downloading：进度 hook 检测后终止
    task.status = "cancelled"
    return {"ok": True, "id": task_id}


@app.get("/api/files")
def list_files():
    files = []
    root = DOWNLOAD_DIR.resolve()
    # 递归扫描（每个任务在独立子目录中），返回相对路径
    scanned = []
    for p in DOWNLOAD_DIR.rglob("*"):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue  # 扫描期间文件被并发删除，跳过而非报错
        scanned.append((p, st.st_mtime, st.st_size))
    scanned.sort(key=lambda x: x[1], reverse=True)
    for p, mtime, size in scanned:
        files.append({
            "name": p.relative_to(root).as_posix(),
            "size": size,
            "mtime": int(mtime),
        })
    return files


def _resolve_in_downloads(filename: str) -> Path:
    """把用户提供的相对路径解析到下载目录内，杜绝路径穿越。"""
    root = DOWNLOAD_DIR.resolve()
    path = (DOWNLOAD_DIR / filename).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(status_code=404, detail="文件不存在")
    return path


@app.get("/files/{filename:path}")
def get_file(filename: str):
    path = _resolve_in_downloads(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, filename=path.name)


@app.delete("/api/files/{filename:path}")
def delete_file(filename: str):
    path = _resolve_in_downloads(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    path.unlink()
    # 若父目录是任务子目录且已空，一并清理
    root = DOWNLOAD_DIR.resolve()
    parent = path.parent
    if parent != root and not any(parent.iterdir()):
        try:
            parent.rmdir()
        except OSError:
            pass
    return {"ok": True, "deleted": path.name}


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
    stripped = text.lstrip()

    # Netscape 格式：以头部注释开头，每行数据需为制表符分隔的字段
    if stripped.startswith("# Netscape HTTP Cookie File"):
        data_lines = [ln for ln in text.splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#")]
        for ln in data_lines:
            fields = ln.rstrip("\n").split("\t")
            if len(fields) < 5:
                raise HTTPException(
                    status_code=400,
                    detail=f"Netscape 格式错误：行字段不足 → {ln[:60]}",
                )
    # JSON 格式：数组或单个对象，每个元素含 name / domain / value
    elif stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except ValueError:
            raise HTTPException(status_code=400, detail="Cookie JSON 解析失败")
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or not parsed:
            raise HTTPException(status_code=400, detail="Cookie JSON 需为非空数组")
        for item in parsed:
            if not isinstance(item, dict) or not all(
                k in item for k in ("name", "domain", "value")
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Cookie JSON 项需包含 name / domain / value 字段",
                )
    else:
        raise HTTPException(
            status_code=400,
            detail="无法识别的 Cookie 格式：需为 Netscape cookies.txt 或 JSON",
        )

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
