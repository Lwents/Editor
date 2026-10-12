"""Local editor AI configuration; never return stored provider credentials."""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import set_key
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.core import AI_CONFIG_FILE, settings
from app.services.store import job_store

router = APIRouter()
_config_lock = asyncio.Lock()
_LOCAL = {"localhost", "127.0.0.1", "::1", "testclient"}


def local_request(request: Request) -> None:
    if not request.client or request.client.host not in _LOCAL:
        raise HTTPException(403, "Cấu hình AI chỉ được mở trên máy chạy Editor.")
    host = urlsplit("http://" + request.headers.get("host", "")).hostname
    if host not in _LOCAL:
        raise HTTPException(403, "Địa chỉ truy cập không hợp lệ.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in _LOCAL:
        raise HTTPException(403, "Origin không hợp lệ.")


class AiConfig(BaseModel):
    api_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)

    @field_validator("api_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Dùng URL http/https, không chứa tài khoản, query hoặc fragment.")
        if any(c.isspace() for c in value):
            raise ValueError("URL không được chứa khoảng trắng.")
        return value

    @field_validator("model", "api_key")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if any(ord(c) < 32 for c in value):
            raise ValueError("Không được chứa ký tự điều khiển.")
        if not value:
            raise ValueError("Không được để trống.")
        return value


def effective_key(config: AiConfig) -> str:
    if config.api_key:
        return config.api_key
    if config.api_url != settings.ninerouter_api_url.rstrip("/"):
        raise HTTPException(400, "Bạn đã đổi địa chỉ API. Hãy nhập key cho địa chỉ mới.")
    if not settings.ninerouter_api_key:
        raise HTTPException(400, "Hãy nhập API key.")
    return settings.ninerouter_api_key


def public_config() -> dict:
    return {"api_url": settings.ninerouter_api_url, "model": settings.ai_model, "has_api_key": bool(settings.ninerouter_api_key)}


@router.get("/settings/ai", dependencies=[Depends(local_request)])
def get_config() -> dict:
    return public_config()


@router.post("/settings/ai", dependencies=[Depends(local_request)])
async def save_config(config: AiConfig) -> dict:
    async with _config_lock:
        if any(job.status in {"queued", "processing"} for job in job_store.list()):
            raise HTTPException(409, "Đợi tác vụ hiện tại hoàn tất trước khi đổi cấu hình AI.")
        key = effective_key(config)
        env_file = AI_CONFIG_FILE
        temp_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=env_file.parent)
            os.close(fd)
            temp_path = Path(name)
            temp_path.write_text(env_file.read_text(encoding="utf-8") if env_file.exists() else "", encoding="utf-8")
            for name, value in {"AUTO_TRANSLATE_NINEROUTER_API_URL": config.api_url, "AUTO_TRANSLATE_NINEROUTER_API_KEY": key, "AUTO_TRANSLATE_AI_MODEL": config.model}.items():
                set_key(str(temp_path), name, value, quote_mode="always")
            os.replace(temp_path, env_file)
        except OSError:
            raise HTTPException(500, "Không lưu được cấu hình backend. Kiểm tra quyền ghi thư mục be.") from None
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)
        settings.ninerouter_api_url = config.api_url
        settings.ninerouter_api_key = key
        settings.ai_model = config.model
        return {**public_config(), "message": "Đã lưu. Cấu hình mới được dùng cho lần dịch tiếp theo."}


@router.post("/settings/ai/test", dependencies=[Depends(local_request)])
async def test_config(config: AiConfig) -> dict:
    from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, APIStatusError
    key = effective_key(config)
    started = time.perf_counter()
    try:
        async with AsyncOpenAI(api_key=key, base_url=config.api_url, timeout=30, max_retries=0) as client:
            response = await client.chat.completions.create(model=config.model, messages=[{"role": "user", "content": "Reply only OK."}], max_tokens=64)
            if not response.choices or not response.choices[0].message.content:
                return {"ok": False, "message": "API kết nối được nhưng model chưa trả nội dung. Kiểm tra model hoặc quota."}
        return {"ok": True, "message": "Kết nối thành công. Model đã trả lời yêu cầu thử.", "latency_ms": round((time.perf_counter() - started) * 1000)}
    except APITimeoutError:
        return {"ok": False, "message": "Quá 30 giây chưa có phản hồi. Kiểm tra dịch vụ AI và thử lại."}
    except APIConnectionError:
        return {"ok": False, "message": "Không kết nối được API. Kiểm tra địa chỉ và dịch vụ 9Router."}
    except APIStatusError as exc:
        messages = {401: "API key không hợp lệ.", 403: "API key không có quyền dùng model.", 404: "Không tìm thấy API hoặc model. Kiểm tra URL (thường kết thúc bằng /v1) và tên model.", 429: "Hết quota hoặc đang vượt giới hạn yêu cầu."}
        return {"ok": False, "message": messages.get(exc.status_code, f"API trả lỗi HTTP {exc.status_code}. Kiểm tra nhà cung cấp AI.")}
    except Exception:
        return {"ok": False, "message": "Phản hồi API không hợp lệ hoặc model chưa hỗ trợ chat completions."}
