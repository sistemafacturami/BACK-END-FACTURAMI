"""
FACTURAMI - Almacén cifrado de secretos por empresa (credenciales y certificado de Hacienda).
==========================================================================================
- Cifrado autenticado Fernet (AES-128-CBC + HMAC-SHA256) de la librería `cryptography`.
- Clave maestra SOLO por entorno: FACTURAMI_FE_SECRET_KEY (>= 32 caracteres), distinta del JWT.
- Clave derivada POR EMPRESA (HMAC-SHA256(master, company_id)): un secreto de una empresa
  no se puede descifrar usando el company_id de otra.
- Si la clave no está definida, NO se guarda nada (falla cerrado). Nunca texto plano.
"""
import base64
import hashlib
import hmac
import os


class SecretStoreError(RuntimeError):
    """Almacén de secretos no disponible o dato inválido."""


def is_configured() -> bool:
    if len(os.environ.get('FACTURAMI_FE_SECRET_KEY', '')) < 32:
        return False
    try:
        import cryptography.fernet  # noqa: F401
    except Exception:
        return False
    return True


def _fernet(company_id: str):
    master = os.environ.get('FACTURAMI_FE_SECRET_KEY', '')
    if len(master) < 32:
        raise SecretStoreError('FACTURAMI_FE_SECRET_KEY no esta definida (minimo 32 caracteres)')
    try:
        from cryptography.fernet import Fernet
    except Exception:
        raise SecretStoreError('Falta la libreria cryptography (pip install -r requirements.txt)')
    cid = str(company_id or '').strip()
    if not cid:
        raise SecretStoreError('company_id requerido para cifrar')
    key = hmac.new(master.encode('utf-8'), b'facturami-fe|' + cid.encode('utf-8'), hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(company_id: str, plaintext) -> str:
    data = plaintext.encode('utf-8') if isinstance(plaintext, str) else bytes(plaintext)
    return _fernet(company_id).encrypt(data).decode('ascii')


def decrypt(company_id: str, token: str) -> bytes:
    from cryptography.fernet import InvalidToken
    try:
        return _fernet(company_id).decrypt(str(token).encode('ascii'))
    except InvalidToken:
        raise SecretStoreError('No se pudo descifrar el secreto (clave o empresa incorrecta)')
