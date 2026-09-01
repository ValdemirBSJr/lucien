import asyncio
import io

import pytesseract
from PIL import Image, UnidentifiedImageError

from app.domain.ports import (
    ImageSecurityScanner,
    ProcessedAsset,
    SecretDetectedError,
    SecretScanner,
    UpstreamError,
    ValidationError,
    secret_detection_message,
)

# Allowlist estrita: so o formato realmente decodificado conta, nunca a
# extensao do nome de arquivo nem o media_type declarado pelo cliente.
_ALLOWED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg"}
_DECODE_ERRORS = (UnidentifiedImageError, OSError, ValueError)


class TesseractImageScanner(ImageSecurityScanner):
    """Decodifica, remove metadado, roda OCR e reusa o SecretScanner de texto.

    Uma politica de segredo so: o texto extraido do OCR passa pelo MESMO
    `SecretScanner` que ja varre o corpo do runbook, em vez de uma segunda
    integracao com o gitleaks.
    """

    def __init__(
        self,
        secret_scanner: SecretScanner,
        max_bytes: int,
        max_dimension_px: int,
        ocr_languages: str,
    ) -> None:
        self._secret_scanner = secret_scanner
        self._max_bytes = max_bytes
        self._max_dimension_px = max_dimension_px
        self._ocr_languages = ocr_languages

    async def process(self, raw_bytes: bytes, declared_media_type: str) -> ProcessedAsset:
        # Barreira barata antes de qualquer decode -- rejeita payload grande
        # sem gastar CPU decodificando algo que ja seria recusado.
        if len(raw_bytes) > self._max_bytes:
            raise ValidationError("image exceeds the configured size limit")

        clean_bytes = await asyncio.to_thread(self._decode_and_reencode, raw_bytes)
        ocr_text = await asyncio.to_thread(self._extract_text, clean_bytes)

        result = await self._secret_scanner.detect(ocr_text)
        if result.detected:
            raise SecretDetectedError(secret_detection_message(result))

        return ProcessedAsset(content=clean_bytes, media_type="image/png")

    def _decode_and_reencode(self, raw_bytes: bytes) -> bytes:
        """Decodifica de verdade, valida formato/dimensao, e reconstroi so dos pixels.

        Reconstruir a partir de `getdata()` -- em vez de reaproveitar o
        arquivo original -- e o que de fato remove EXIF/tEXt/ICC: alguns
        codecs ignoram um `img.info.clear()` na hora de salvar, mas nenhum
        chunk de metadado sobrevive a uma imagem nova criada so com pixels.
        """

        try:
            with Image.open(io.BytesIO(raw_bytes)) as probe:
                probe.verify()
        except _DECODE_ERRORS as error:
            raise ValidationError(
                "could not decode image; only PNG/JPEG are accepted"
            ) from error

        # verify() inutiliza o objeto para leitura de pixel; reabre do zero.
        try:
            with Image.open(io.BytesIO(raw_bytes)) as decoded:
                if decoded.format not in _ALLOWED_FORMATS:
                    raise ValidationError(
                        f"unsupported image format '{decoded.format}'; "
                        "only PNG/JPEG are accepted"
                    )
                width, height = decoded.size
                if width > self._max_dimension_px or height > self._max_dimension_px:
                    raise ValidationError(
                        "image exceeds the configured dimension limit"
                    )
                clean = Image.new(decoded.mode, decoded.size)
                clean.putdata(list(decoded.getdata()))
                buffer = io.BytesIO()
                clean.save(buffer, format="PNG")
                return buffer.getvalue()
        except _DECODE_ERRORS as error:
            raise ValidationError(
                "could not decode image; only PNG/JPEG are accepted"
            ) from error

    def _extract_text(self, png_bytes: bytes) -> str:
        try:
            with Image.open(io.BytesIO(png_bytes)) as image:
                return pytesseract.image_to_string(image, lang=self._ocr_languages)
        except pytesseract.TesseractError as error:
            # Binario ausente, pacote de idioma faltando, ou falha do proprio
            # engine -- falha fechada, igual a uma queda do secret-scanner.
            raise UpstreamError(
                "OCR engine unavailable; the image was not accepted"
            ) from error
