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
- GET  /api/proxy     查询代理状态
- PUT  /api/proxy     保存代理地址
- DELETE /api/proxy   清除代理
- POST /api/proxy/test 探测代理能否连通 YouTube
- GET  /api/about     应用信息（版本 / yt-dlp 版本 / 作者）
- 静态前端挂载在 /
"""

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import yt_dlp
from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .downloader import FORMATS, TMP_SUFFIXES, DownloadManager, normalize_proxy

# 镜像标签版本：Docker 构建时由 workflow 注入（DATE-SHORT_SHA），本地开发回退为 dev
APP_VERSION = os.environ.get("APP_VERSION", "dev")

# ---------------------------------------------------------------------------
# 下载目录、Cookie 文件与代理配置（容器内可通过环境变量覆盖）
# ---------------------------------------------------------------------------
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/downloads"))
COOKIE_FILE = os.environ.get("COOKIE_FILE", "/config/cookies.txt")
PROXY_FILE = os.environ.get("PROXY_FILE", "/config/proxy.txt")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

manager = DownloadManager(DOWNLOAD_DIR, COOKIE_FILE, PROXY_FILE)

# 代理连通性探测目标：Google 的 204 探针端点，无正文、开销极小；
# 且它与 YouTube 同属被墙域名，能真实反映「这个代理到底能不能出去看 YouTube」。
PROXY_TEST_URL = "https://www.youtube.com/generate_204"
PROXY_TEST_TIMEOUT = 10  # 单位：秒。国内配代理时超时太短容易误报失败

app = FastAPI(title="yt-dlp WebUI", version="1.0.0")


class DownloadRequest(BaseModel):
    url: str = Field(..., max_length=2048, description="视频 / 音频 URL")
    format: str = Field("mp4", description="输出格式：mp4 / best / mp3")


class ProxyRequest(BaseModel):
    url: str = Field(..., max_length=2048,
                     description="代理地址，如 http://host.docker.internal:7890")


class ProxyTestRequest(BaseModel):
    url: Optional[str] = Field(None, max_length=2048,
                               description="待测代理；省略时测试当前已保存的代理")


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
def list_tasks(limit: int = Query(30, ge=1, le=50)):
    return {"total": manager.count(), "tasks": manager.snapshot(limit)}


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
    # 递归扫描（每个任务在独立子目录中），返回相对路径；排除 .part 等临时文件
    scanned = []
    try:
        it = DOWNLOAD_DIR.rglob("*")
        for p in it:
            if not p.is_file() or p.name.endswith(TMP_SUFFIXES):
                continue
            try:
                st = p.stat()
            except OSError:
                continue  # 扫描期间文件被并发删除，跳过而非报错
            scanned.append((p, st.st_mtime, st.st_size))
    except OSError:
        pass  # rglob 遍历中目录被并发删除，整体容错
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
# 网络代理：状态 / 保存 / 清除 / 连通性测试
# ---------------------------------------------------------------------------
def _localhost_hint(proxy: str) -> Optional[str]:
    """容器内填 127.0.0.1 是最常见的误配：那指向容器自己，而不是宿主机。

    仅当自身确实运行在容器中时才提示；直接跑在宿主机上时，127.0.0.1 可能是对的。
    """
    if not Path("/.dockerenv").exists():
        return None
    host = (urllib.parse.urlparse(proxy).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return None
    return (
        "检测到地址是 127.0.0.1：当前运行在容器内，它指向容器自身而不是宿主机。"
        "若代理装在宿主机，请改成 host.docker.internal（或代理所在机器的局域网 IP）"
    )


def _humanize_proxy_error(exc: Exception) -> str:
    """把底层网络异常翻译成能照着排查的中文提示。"""
    msg = str(exc)
    lower = msg.lower()
    if "connection refused" in lower:
        return "连接被拒绝：代理地址/端口不对，或宿主机代理未在该端口监听"
    if "timed out" in lower or "timeout" in lower:
        return ("连接超时：代理无响应。Linux/NAS 上请确认代理已开启「允许局域网」"
                "（Clash 的 allow-lan），并已放行防火墙端口")
    if "socks" in lower or "pysocks" in lower:
        return f"SOCKS 代理不可用：{msg[:200]}"
    if "certificate" in lower or "ssl" in lower:
        return f"TLS/证书校验失败：{msg[:200]}"
    return msg[:300] or exc.__class__.__name__


def _probe_proxy(proxy: str) -> dict:
    """带着代理向 YouTube 发一次轻量请求，返回结构化结果。

    刻意不抛异常：前端需要区分「代理不通」和「代理配错」，
    把原因显示在界面上，比抛出 500 有用得多。
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": PROXY_TEST_TIMEOUT,
        "proxy": proxy,  # 与真实下载走同一条 yt-dlp 请求链路
    }
    started = time.monotonic()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            with ydl.urlopen(PROXY_TEST_URL) as resp:
                status = resp.status
    except Exception as exc:  # noqa: BLE001 - 任意网络/代理异常都要反馈给前端
        return {"ok": False, "error": _humanize_proxy_error(exc),
                "target": PROXY_TEST_URL}
    latency_ms = int((time.monotonic() - started) * 1000)
    if status != 204:
        return {"ok": False, "error": f"目标返回 HTTP {status}",
                "target": PROXY_TEST_URL}
    return {"ok": True, "latency_ms": latency_ms, "target": PROXY_TEST_URL}


@app.get("/api/proxy")
def proxy_status():
    return manager.proxy_status()


@app.put("/api/proxy")
def save_proxy(req: ProxyRequest):
    try:
        url = manager.save_proxy(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = {"ok": True, "url": url}
    hint = _localhost_hint(url)
    if hint:
        result["hint"] = hint  # 保存时就把提示给出，不必等到测试
    return result


@app.delete("/api/proxy")
def delete_proxy():
    manager.remove_proxy()
    return {"ok": True, "deleted": True}


@app.post("/api/proxy/test")
def test_proxy(req: Optional[ProxyTestRequest] = None):
    """探测代理能否连通 YouTube。

    优先测试请求里传入的地址（还没保存也能先试），没传则测试当前已保存的代理。
    """
    if req is not None and req.url:
        try:
            url = normalize_proxy(req.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        url = manager.proxy
        if not url:
            raise HTTPException(status_code=400, detail="尚未配置代理，请先填写代理地址")
    result = _probe_proxy(url)
    hint = _localhost_hint(url)
    if hint:
        result["hint"] = hint
    return result


# ---------------------------------------------------------------------------
# 应用信息（「关于」界面数据来源）
# ---------------------------------------------------------------------------
@app.get("/api/about")
def about():
    return {
        "name": "yt-dlp WebUI",
        "version": APP_VERSION,                    # 镜像标签版本（如 20261016-abc1234）
        "yt_dlp_version": yt_dlp.version.__version__,
        "author": "Dremous",
        "repo": "https://github.com/Dremous/yt-dlp-webui",
    }


# ---------------------------------------------------------------------------
# 静态前端
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
