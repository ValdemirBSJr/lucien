import base64
import binascii
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from app.domain.models import SealedUpload
from app.domain.ports import UploadCipher, ValidationError


class AESGCMUploadCipher(UploadCipher):
    """Protege o payload temporário com chaves derivadas e domínios distintos."""

    _AAD_PREFIX = b"lucien-upload-queue-v1"

    def __init__(self, root_secret: str) -> None:
        secret = root_secret.encode("utf-8")
        self._encryption_key = hmac.new(
            secret, b"queue-encryption-v1", hashlib.sha256
        ).digest()
        self._fingerprint_key = hmac.new(
            secret, b"queue-fingerprint-v1", hashlib.sha256
        ).digest()

    @classmethod
    def _aad(cls, owner_id: str, name: str) -> bytes:
        return cls._AAD_PREFIX + b"\0" + owner_id.encode() + b"\0" + name.encode()

    def seal(
        self,
        owner_id: str,
        name: str,
        sanitized_log: str,
        description: str | None,
    ) -> SealedUpload:
        payload = json.dumps(
            {"log": sanitized_log, "description": description},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._encryption_key).encrypt(
            nonce, payload, self._aad(owner_id, name)
        )
        fingerprint = hmac.new(
            self._fingerprint_key, payload, hashlib.sha256
        ).hexdigest()
        return SealedUpload(
            ciphertext=base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii"),
            fingerprint=fingerprint,
        )

    def open(
        self, owner_id: str, name: str, ciphertext: str
    ) -> tuple[str, str | None]:
        try:
            encoded = base64.b64decode(ciphertext, altchars=b"-_", validate=True)
            if len(encoded) < 29:
                raise ValueError("payload cifrado incompleto")
            payload = AESGCM(self._encryption_key).decrypt(
                encoded[:12], encoded[12:], self._aad(owner_id, name)
            )
            decoded = json.loads(payload)
            log = decoded["log"]
            description = decoded.get("description")
            if not isinstance(log, str) or (
                description is not None and not isinstance(description, str)
            ):
                raise TypeError("payload cifrado possui tipos inválidos")
            return log, description
        except (binascii.Error, InvalidTag, KeyError, TypeError, ValueError) as error:
            raise ValidationError("payload assíncrono inválido") from error
