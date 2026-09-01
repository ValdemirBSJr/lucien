import io

import pytest
from PIL import Image, ImageDraw

from app.domain.ports import (
    SecretDetectedError,
    SecretScanner,
    SecretScanResult,
    ValidationError,
)
from app.infrastructure.image_scanner import TesseractImageScanner


class _Scanner(SecretScanner):
    def __init__(self, detected: bool = False) -> None:
        self.detected = detected
        self.scanned: list[str] = []

    async def detect(self, content: str) -> SecretScanResult:
        self.scanned.append(content)
        return SecretScanResult(
            detected=self.detected,
            rules=("lucien-snmp-community",) if self.detected else (),
        )


def _scanner(detected: bool = False, **overrides) -> tuple[TesseractImageScanner, _Scanner]:
    fake = _Scanner(detected=detected)
    params = {
        "max_bytes": 5 * 1024 * 1024,
        "max_dimension_px": 4096,
        "ocr_languages": "eng",
        **overrides,
    }
    return TesseractImageScanner(secret_scanner=fake, **params), fake


def _png_bytes(width: int = 200, height: int = 80, text: str | None = None) -> bytes:
    image = Image.new("RGB", (width, height), color="white")
    if text:
        draw = ImageDraw.Draw(image)
        draw.text((5, 5), text, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _png_with_metadata(width: int = 50, height: int = 50) -> bytes:
    image = Image.new("RGB", (width, height), color="white")
    buffer = io.BytesIO()
    metadata = {"Comment": "sensitive-camera-serial-XYZ123"}
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for key, value in metadata.items():
        info.add_text(key, value)
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def test_rejects_non_image_bytes() -> None:
    scanner, _ = _scanner()
    with pytest.raises(ValidationError, match="could not decode"):
        _run(scanner.process(b"not an image", "image/png"))


def test_rejects_oversized_raw_bytes() -> None:
    scanner, fake = _scanner(max_bytes=10)
    with pytest.raises(ValidationError, match="size limit"):
        _run(scanner.process(_png_bytes(), "image/png"))
    assert fake.scanned == []  # nunca chegou a rodar OCR/scan


def test_rejects_oversized_dimensions() -> None:
    scanner, _ = _scanner(max_dimension_px=100)
    with pytest.raises(ValidationError, match="dimension limit"):
        _run(scanner.process(_png_bytes(width=200, height=50), "image/png"))


def test_happy_path_returns_png_and_scans_ocr_text() -> None:
    scanner, fake = _scanner()
    result = _run(scanner.process(_png_bytes(text="hello"), "image/png"))
    assert result.media_type == "image/png"
    # A imagem retornada e valida PNG.
    Image.open(io.BytesIO(result.content)).verify()
    assert len(fake.scanned) == 1


def test_strips_metadata() -> None:
    scanner, _ = _scanner()
    original = _png_with_metadata()
    result = _run(scanner.process(original, "image/png"))
    reopened = Image.open(io.BytesIO(result.content))
    assert not reopened.info


def test_secret_detected_in_ocr_text_is_rejected_without_leaking_value() -> None:
    scanner, _ = _scanner(detected=True)
    with pytest.raises(SecretDetectedError, match="lucien-snmp-community"):
        _run(scanner.process(_png_bytes(text="community public"), "image/png"))


def _run(coro):
    import asyncio

    return asyncio.run(coro)
