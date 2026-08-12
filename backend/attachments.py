# v1.0.19.4 — 用户上传附件：凭证护栏 + 多模态打包 + 注入
"""
attachments.py — 把「用户选/拖进来的本地文件」安全地喂给 LLM。

这里只做三件小事，一件都不多（DESIGN §七「用简单的代码」）：

  1. **凭证护栏（DESIGN 决策 #9）**。后端直读本地路径，天生有「被诱导读任意文件」
     的风险：user_message 里的 path 只是一个字符串，谁都能塞 C:\\Windows\\... 或 /etc/passwd。
     护栏是一枚 HMAC 签名：只有 Electron 主进程（它亲眼看着用户点了对话框 / 把文件拖进
     窗口）用**主进程与后端共享的 runtime_token** 对绝对路径签名，后端才认。
     消息正文里凭空捏造的路径没有合法签名 → 一律拒读。签的是**路径本身**，
     所以同一份用户选过的文件，回看时重新读取也天然放行（DESIGN 决策 #3）。

  2. **原样打包（DESIGN 决策 #4/#5）**。图片 → OpenAI 多模态 image_url（data URL）；
     其余 → OpenAI file 内容块（base64 file_data）。**只做 base64 装信封，不转码、不 OCR、
     不缩图**——字节原样。模型收不收由模型自己决定（收不到就 HTTP 400，上层友好打回）。

  3. **注入（本次故障根因的修复点）**。用户消息在后端全程是一个**纯字符串 content**，
     两个 Agent 都把它拼成 {"role":"user","content": <str>} 发给 provider——附件在这条链路上
     根本没有落脚点，于是「界面层完整、LLM 输入层丢失」。这里提供把「文本 + 附件块」
     合成 OpenAI 多模态数组的工具，在**投影之后、发 provider 之前**替换掉最后一条 user 消息。
     只影响这一回合发出去的投影副本，Agent 的权威历史仍存纯文本（不把 base64 塞进历史/落盘）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import mimetypes
import os
from typing import Any

log = logging.getLogger("knowe.attachments")

# 常见图片扩展名 → 走 image_url（视觉模型能看）。其余走 file 块。
_IMAGE_MIME_PREFIX = "image/"
_EXT_MIME_FALLBACK = {
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
    "log": "text/plain",
    "json": "application/json",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "py": "text/x-python",
    "js": "text/javascript",
    "ts": "text/plain",
    "yaml": "text/plain",
    "yml": "text/plain",
    "pdf": "application/pdf",
    "webp": "image/webp",
}


class AttachmentError(Exception):
    """一个能直接说给用户听的附件错误。kind 供上层决定文案分类。"""

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def normalize_path(path: Any) -> str:
    """签名与读取共用的规范绝对路径。主进程 path.resolve 与此在同机同 OS 下一致。"""
    return os.path.abspath(os.path.expanduser(str(path or "")))


def sign_path(path: Any, token: str) -> str:
    """用共享 runtime_token 对绝对路径签名（主进程与后端算法必须逐字一致）。"""
    key = (token or "").encode("utf-8")
    msg = normalize_path(path).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_path(path: Any, sig: Any, token: str) -> bool:
    """常量时间校验：这条 path 是不是主进程用 token 亲手签发的。"""
    if not token or not isinstance(sig, str) or not sig:
        return False
    try:
        expected = sign_path(path, token)
    except Exception:
        return False
    return hmac.compare_digest(expected, sig)


def _basename(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").rstrip("/")
    tail = normalized.rsplit("/", 1)[-1]
    return tail or (str(path) or "file")


def _guess_mime(name: str, ext: str) -> str:
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    key = (ext or "").lower().lstrip(".")
    if key in _EXT_MIME_FALLBACK:
        return _EXT_MIME_FALLBACK[key]
    return "application/octet-stream"


def load_part(record: dict[str, Any], token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """把一条附件记录变成 (provider 内容块, 回显用元数据)。

    护栏在这里落地：签名不对 → 拒读（kind=guard）；文件不在 → 友好打回（kind=missing）。
    """
    if not isinstance(record, dict):
        raise AttachmentError("附件数据格式不对。", kind="invalid")
    path = record.get("path")
    name = str(record.get("name") or _basename(str(path or "")))
    ext = str(record.get("ext") or (name.rsplit(".", 1)[-1] if "." in name else ""))
    sig = record.get("sig")

    if not path or not verify_path(path, sig, token):
        # 消息正文捏造的路径走到这里——没有主进程签名，读都不读。
        raise AttachmentError(
            f"附件「{name}」没有通过安全校验，已被拒绝（只有通过「添加附件」或拖拽选进来的文件才会被发送）。",
            kind="guard",
        )
    abs_path = normalize_path(path)
    if not os.path.isfile(abs_path):
        raise AttachmentError(f"原文件「{name}」已被移动、重命名或删除。", kind="missing")

    try:
        with open(abs_path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise AttachmentError(f"读取附件「{name}」失败：{exc}", kind="missing") from None

    b64 = base64.b64encode(data).decode("ascii")
    mime = _guess_mime(name, ext)
    if mime.startswith(_IMAGE_MIME_PREFIX):
        part: dict[str, Any] = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        }
    else:
        # OpenAI 的 file 内容块（PDF/文本等）；模型不支持就 400，上层友好打回。
        part = {
            "type": "file",
            "file": {"filename": name, "file_data": f"data:{mime};base64,{b64}"},
        }
    meta = {
        "path": abs_path,
        "name": name,
        "ext": ext,
        "size": len(data),
        "sig": sig,
    }
    return part, meta


def build_parts(
    records: list[dict[str, Any]] | None, token: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读一批附件 → (provider 内容块列表, 元数据列表)。任一条失败即抛（原子性）。"""
    parts: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []
    for record in records or []:
        part, meta = load_part(record, token)
        parts.append(part)
        metas.append(meta)
    return parts, metas


def echo_meta(record: dict[str, Any]) -> dict[str, Any]:
    """user_echo / 历史里带的附件元数据——只带路径与身份，**绝不带字节**（DESIGN 决策 #3）。"""
    path = record.get("path")
    name = str(record.get("name") or _basename(str(path or "")))
    ext = str(record.get("ext") or (name.rsplit(".", 1)[-1] if "." in name else ""))
    out: dict[str, Any] = {"path": normalize_path(path), "name": name, "ext": ext}
    size = record.get("size")
    if isinstance(size, (int, float)) and size >= 0:
        out["size"] = int(size)
    sig = record.get("sig")
    if isinstance(sig, str) and sig:
        out["sig"] = sig
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return str(item.get("text") or "")
    return ""


def user_content(text: str, parts: list[dict[str, Any]] | None) -> Any:
    """无附件 → 纯文本字符串（和以前完全一样）；有附件 → OpenAI 多模态数组。"""
    if not parts:
        return text
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(parts)
    return blocks


def inject_into_last_user(
    messages: list[dict[str, Any]], parts: list[dict[str, Any]] | None,
) -> bool:
    """把附件块并进**最后一条 user 消息**（当前回合），返回是否命中。

    只改传给 provider 的投影副本：当前回合永远是投影尾部的 verbatim 消息，
    历史里更早的 user 消息保持纯文本、不被重发 base64。
    """
    if not parts:
        return False
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            message["content"] = user_content(_text_of(message.get("content")), parts)
            return True
    return False


__all__ = [
    "AttachmentError",
    "build_parts",
    "echo_meta",
    "inject_into_last_user",
    "load_part",
    "normalize_path",
    "sign_path",
    "user_content",
    "verify_path",
]
