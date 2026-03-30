import base64
from io import BytesIO

from docx import Document

from app.models.schemas import Attachment, ChatRequest, Message
from app.routers.chat import (
    MAX_DOCUMENT_TEXT_CHARS,
    build_messages,
    build_user_content,
    extract_docx_text,
)


def text_attachment(name: str, text: str, mime_type: str = "text/plain") -> Attachment:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return Attachment(name=name, mime_type=mime_type, data_url=f"data:{mime_type};base64,{encoded}")


def image_attachment(name: str = "topologia.png") -> Attachment:
    encoded = base64.b64encode(b"fake-image-bytes").decode("ascii")
    return Attachment(name=name, mime_type="image/png", data_url=f"data:image/png;base64,{encoded}")


def make_docx_bytes(*paragraphs: str) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_extract_docx_text_reads_paragraphs():
    raw_bytes = make_docx_bytes("Router 1", "show ip route")

    extracted = extract_docx_text(raw_bytes)

    assert "Router 1" in extracted
    assert "show ip route" in extracted


def test_build_user_content_merges_text_and_text_attachment():
    message = Message(
        role="user",
        content="Revisa este log",
        attachments=[text_attachment("router.log", "Interface down on Fa0/1")],
    )

    content, requires_vision = build_user_content(message)

    assert requires_vision is False
    assert isinstance(content, str)
    assert "Revisa este log" in content
    assert "Archivo adjunto (router.log):" in content
    assert "Interface down on Fa0/1" in content


def test_build_user_content_returns_multimodal_payload_for_images():
    message = Message(
        role="user",
        content="Analiza la captura",
        attachments=[image_attachment()],
    )

    content, requires_vision = build_user_content(message)

    assert requires_vision is True
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "Analiza la captura" in content[0]["text"]
    assert content[1]["type"] == "image_url"


def test_build_user_content_uses_fallback_prompt_for_attachment_only_images():
    message = Message(role="user", content="", attachments=[image_attachment()])

    content, requires_vision = build_user_content(message)

    assert requires_vision is True
    assert content[0]["text"] == "Imagen adjunta: topologia.png"


def test_build_user_content_truncates_docx_text_to_configured_limit():
    oversized_text = "A" * (MAX_DOCUMENT_TEXT_CHARS + 500)
    raw_bytes = make_docx_bytes(oversized_text)
    encoded = base64.b64encode(raw_bytes).decode("ascii")
    message = Message(
        role="user",
        content="Documento",
        attachments=[
            Attachment(
                name="laboratorio.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                data_url=f"data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{encoded}",
            )
        ],
    )

    content, requires_vision = build_user_content(message)

    assert requires_vision is False
    assert isinstance(content, str)
    assert len(content) < len(oversized_text) + 100
    assert "Documento adjunto (laboratorio.docx):" in content


def test_build_messages_trims_history_and_skips_system_messages():
    request = ChatRequest(
        messages=[
            Message(role="system", content="ignorar"),
            *[Message(role="user", content=f"mensaje {index}") for index in range(14)],
            Message(role="assistant", content="respuesta final"),
        ]
    )

    messages, requires_vision = build_messages(request)

    assert requires_vision is False
    assert messages[0]["role"] == "system"
    assert all(message["role"] != "system" for message in messages[1:])
    assert len(messages) == 13
    assert messages[1]["content"] == "mensaje 3"
    assert messages[-1]["content"] == "respuesta final"