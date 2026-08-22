# [v1.0.39.2] 附件格式降级（多模态能力修复）单测
"""
覆盖：
  1. extract_file_text：txt/pdf/docx/xlsx/pptx/xls 提取、截断、无文字层 None
  2. replace_file_blocks_with_text：多附件混合、image_url 不碰、失败保留
  3. build_format_fallback：替换成功返回 messages、全败返回 None、能力缓存标记
  4. file_unsupported / mark_file_unsupported：键隔离
  5. ProviderError.format_rejected：400 判定（provider_client）
  6. chat_stream 降级重发：400 → 转文本 → 重发成功；二次失败不无限重试；无回调老行为
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 统一用顶层命名空间（与本项目 PYTHONPATH=backend 的解析方式一致）。
# 注意：**不能**混用 backend.knowe_core 顶层包导入——provider_client 内部
# 走 knowe_core.* 顶层空间，混用会得到两个不同的 ProviderError 类，
# pytest.raises 将捕获不到（本次修复踩过的坑）。
from attachments import (                                  # noqa: E402
    build_format_fallback,
    extract_file_text,
    file_unsupported,
    mark_file_unsupported,
    replace_file_blocks_with_text,
)
# ProviderError / ProviderClient 走 knowe_core 顶层（与 provider_client 内部一致），
# 避免 backend.knowe_core 双命名空间导致 pytest.raises 捕获不到。
from knowe_core.errors import ProviderError                # noqa: E402
from knowe_core.provider_client import ProviderClient      # noqa: E402
# i18n_backend 含相对导入（from . import runtime_settings），只能经 backend 包加载。
from backend.i18n_backend import msg                       # noqa: E402


# ═══════════════════════════════════════════════════════════════
# 工具：造真实小文件
# ═══════════════════════════════════════════════════════════════

def _b64(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _file_part(filename: str, raw: bytes, mime: str) -> dict:
    return {"type": "file", "file": {"filename": filename, "file_data": _b64(raw, mime)}}


def _make_pdf(text: str = "Hello Knowe PDF") -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_docx(text: str = "Word 段落一\nWord 段落二") -> bytes:
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_xlsx() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["姓名", "分数"])
    ws.append(["张三", 96])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_pptx(text: str = "演示页标题") -> bytes:
    pptx_mod = pytest.importorskip("pptx")
    prs = pptx_mod.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = text
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════
# 1. extract_file_text
# ═══════════════════════════════════════════════════════════════

class TestExtractFileText:
    def test_txt_direct(self):
        part = _file_part("notes.txt", "你好 Knowe".encode(), "text/plain")
        assert extract_file_text(part) == "你好 Knowe"

    def test_pdf_text_layer(self):
        part = _file_part("doc.pdf", _make_pdf(), "application/pdf")
        text = extract_file_text(part)
        assert text is not None and "Hello Knowe PDF" in text

    def test_docx_paragraphs(self):
        part = _file_part("doc.docx", _make_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        text = extract_file_text(part)
        assert text is not None and "Word 段落一" in text and "Word 段落二" in text

    def test_xlsx_cells(self):
        part = _file_part("table.xlsx", _make_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        text = extract_file_text(part)
        assert text is not None and "张三" in text and "96" in text

    def test_pptx_title(self):
        part = _file_part("deck.pptx", _make_pptx(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        text = extract_file_text(part)
        assert text is not None and "演示页标题" in text

    def test_truncated(self):
        part = _file_part("big.txt", ("x" * 5000).encode(), "text/plain")
        text = extract_file_text(part, max_chars=100)
        assert text is not None and len(text) <= 120 and "已截断" in text

    def test_unknown_binary_returns_none(self):
        part = _file_part("blob.xyz", b"\x00\x01\x02garbage", "application/octet-stream")
        assert extract_file_text(part) is None

    def test_corrupt_base64_returns_none(self):
        part = {"type": "file", "file": {"filename": "a.pdf", "file_data": "data:application/pdf;base64,@@not-base64@@"}}
        assert extract_file_text(part) is None


# ═══════════════════════════════════════════════════════════════
# 2. replace_file_blocks_with_text
# ═══════════════════════════════════════════════════════════════

class TestReplaceBlocks:
    def test_mixed_attachments_image_untouched(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "看下这些"},
            _file_part("doc.pdf", _make_pdf(), "application/pdf"),
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,XXXX"}},
        ]}]
        outcome = replace_file_blocks_with_text(messages)
        assert outcome["replaced"] == 1 and outcome["failed"] == []
        types = [b["type"] for b in messages[0]["content"]]
        assert types == ["text", "text", "image_url"]
        # 替换后的 text 块带文件名前缀
        assert messages[0]["content"][1]["text"].startswith("【文件 doc.pdf 内容】")
        # image_url 原样
        assert messages[0]["content"][2]["image_url"]["url"] == "data:image/png;base64,XXXX"

    def test_failed_file_replaced_with_notice_block(self):
        # 提取失败的 file 块 → 换成说明文本块（绝不能残留 file 块，
        # 否则降级重发照样 400——本次实测踩的坑）。
        messages = [{"role": "user", "content": [
            _file_part("blob.xyz", b"\x00\x01\x02", "application/octet-stream"),
        ]}]
        outcome = replace_file_blocks_with_text(messages)
        assert outcome["replaced"] == 0 and outcome["failed"] == ["blob.xyz"]
        assert messages[0]["content"][0]["type"] == "text"
        assert "无法读取内容" in messages[0]["content"][0]["text"]

    def test_scanned_pdf_replaced_with_notice(self):
        # 扫描版 pdf：fitz 提取 0 字符 → 说明块，不残留 file 块
        part = {"type": "file", "file": {
            "filename": "scan.pdf",
            "file_data": "data:application/pdf;base64," + base64.b64encode(_make_pdf("")).decode(),
        }}
        messages = [{"role": "user", "content": [part]}]
        outcome = replace_file_blocks_with_text(messages)
        assert outcome["replaced"] == 0 and outcome["failed"] == ["scan.pdf"]
        assert messages[0]["content"][0]["type"] == "text"
        assert "无法读取内容" in messages[0]["content"][0]["text"]

    def test_no_user_content_list_is_noop(self):
        messages = [{"role": "user", "content": "纯文本无附件"}]
        outcome = replace_file_blocks_with_text(messages)
        assert outcome["replaced"] == 0 and outcome["failed"] == []
        assert messages[0]["content"] == "纯文本无附件"


# ═══════════════════════════════════════════════════════════════
# 3. 能力缓存
# ═══════════════════════════════════════════════════════════════

class TestCapabilityCache:
    def test_key_isolation_by_model(self):
        mark_file_unsupported("custom", "https://opencode.ai/zen/go/v1", "qwen3.6-plus")
        assert file_unsupported("custom", "https://opencode.ai/zen/go/v1", "qwen3.6-plus")
        assert not file_unsupported("custom", "https://opencode.ai/zen/go/v1", "other-model")
        assert not file_unsupported("openai-api", "https://api.openai.com/v1", "qwen3.6-plus")

    def test_all_none_never_marks(self):
        mark_file_unsupported(None, None, None)
        assert not file_unsupported(None, None, None)

    def test_key_normalizes_protected_form_and_settings_form(self):
        # [v1.0.39.2] 实锤回归：engine 注入保护形态（{base}/chat/completions#）
        # 与 settings 原始形态（{base}）是同一网关，必须互相命中。
        mark_file_unsupported(
            "custom", "https://norm.test/v1/chat/completions#", "qwen3.6-plus"
        )
        assert file_unsupported("custom", "https://norm.test/v1", "qwen3.6-plus")
        # 反向：以原始形态 mark，保护形态查询同样命中
        mark_file_unsupported("custom", "https://norm2.test/v2", "qwen3.6-plus")
        assert file_unsupported(
            "custom", "https://norm2.test/v2/chat/completions#", "qwen3.6-plus"
        )
        # 带尾斜杠也归一
        assert file_unsupported("custom", "https://norm2.test/v2/", "qwen3.6-plus")

    def test_key_normalize_still_isolates_different_gateways(self):
        mark_file_unsupported("custom", "https://norm3.test/v1", "qwen3.6-plus")
        assert not file_unsupported("custom", "https://norm3.test/v2", "qwen3.6-plus")
        assert not file_unsupported("custom", "https://other.test/v1", "qwen3.6-plus")
        assert not file_unsupported(
            "custom", "https://norm3.test/v1/chat/completions#", "other-model"
        )


# ═══════════════════════════════════════════════════════════════
# 4. build_format_fallback
# ═══════════════════════════════════════════════════════════════

class TestFallbackFactory:
    def test_success_returns_messages_and_marks_cache(self):
        fb = build_format_fallback("custom", "https://x/v1", "qwen3.6-plus")
        messages = [{"role": "user", "content": [
            _file_part("doc.pdf", _make_pdf(), "application/pdf"),
        ]}]
        result = fb(messages)
        assert result is messages
        assert messages[0]["content"][0]["type"] == "text"
        assert file_unsupported("custom", "https://x/v1", "qwen3.6-plus")

    def test_total_failure_still_returns_notice_messages(self):
        # 提取全败也要返回 messages（file 块已全换成说明块，重发必然通过）
        fb = build_format_fallback("custom", "https://x/v1", "qwen3.6-plus")
        messages = [{"role": "user", "content": [
            _file_part("blob.xyz", b"\x00\x01", "application/octet-stream"),
        ]}]
        result = fb(messages)
        assert result is messages
        assert messages[0]["content"][0]["type"] == "text"
        assert file_unsupported("custom", "https://x/v1", "qwen3.6-plus")

    def test_no_file_blocks_returns_none(self):
        fb = build_format_fallback("custom", "https://x/v1", "qwen3.6-plus")
        messages = [{"role": "user", "content": "没有附件"}]
        assert fb(messages) is None


# ═══════════════════════════════════════════════════════════════
# 5. ProviderClient 400 判定 + 降级重发
# ═══════════════════════════════════════════════════════════════

_REJECT_BODY = json.dumps({
    "error": {"message": "Error from provider: [invalid_value] Invalid value: file. "
                          "Supported values are: 'text','image_url','video_url' and 'video'."}
})


class _CountingHandler:
    """MockTransport handler：第一次 400（file 被拒），之后 200 SSE。"""

    def __init__(self, fail_times: int = 1):
        self.fail_times = fail_times
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if len(self.bodies) <= self.fail_times:
            return httpx.Response(400, json=json.loads(_REJECT_BODY), request=request)
        sse = 'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode(), request=request,
                              headers={"Content-Type": "text/event-stream"})


@pytest.mark.asyncio
async def test_degrade_retry_success():
    handler = _CountingHandler(fail_times=1)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fb = build_format_fallback("custom", "https://x/v1", "m1")
    pc = ProviderClient(base_url="https://x/v1", api_key="k", model="m1",
                        client_factory=lambda: client, max_retries=0,
                        on_format_rejected=fb)
    messages = [{"role": "user", "content": [
        _file_part("doc.pdf", _make_pdf(), "application/pdf"),
    ]}]
    events = []
    async for ev in pc.chat_stream(messages=messages, tools=None):
        events.append(ev)
    assert len(handler.bodies) == 2
    # 重发的 body 里已无 file 块
    types = [b["type"] for b in handler.bodies[1]["messages"][-1]["content"]]
    assert types == ["text"]
    await client.aclose()


@pytest.mark.asyncio
async def test_no_callback_keeps_old_behavior():
    handler = _CountingHandler(fail_times=99)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pc = ProviderClient(base_url="https://x/v1", api_key="k", model="m1",
                        client_factory=lambda: client, max_retries=2)
    with pytest.raises(ProviderError) as exc_info:
        async for _ in pc.chat_stream(messages=[{"role": "user", "content": "hi"}], tools=None):
            pass
    assert exc_info.value.format_rejected == "file"
    assert len(handler.bodies) == 1  # 未重试（400 不可 retry）
    await client.aclose()


@pytest.mark.asyncio
async def test_retry_not_infinite_on_second_failure():
    handler = _CountingHandler(fail_times=99)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fb = build_format_fallback("custom", "https://x/v1", "m1")
    pc = ProviderClient(base_url="https://x/v1", api_key="k", model="m1",
                        client_factory=lambda: client, max_retries=0,
                        on_format_rejected=fb)
    messages = [{"role": "user", "content": [
        _file_part("doc.pdf", _make_pdf(), "application/pdf"),
    ]}]
    with pytest.raises(ProviderError):
        async for _ in pc.chat_stream(messages=messages, tools=None):
            pass
    assert len(handler.bodies) == 2  # 原请求 + 一次降级重发，不再多
    await client.aclose()


# ═══════════════════════════════════════════════════════════════
# 6. locale 文案键存在
# ═══════════════════════════════════════════════════════════════

class TestLocaleKeys:
    def test_keys_exist(self):
        for key in (
            "attachments.extract.fail",
            "attachments.extract.truncated",
            "attachments.format.rejected",
            "attachments.file.skipped",
        ):
            assert msg(key).strip(), f"locale key missing: {key}"

    def test_skipped_message_interpolates(self):
        text = msg("attachments.file.skipped", name="a.bin")
        assert "a.bin" in text