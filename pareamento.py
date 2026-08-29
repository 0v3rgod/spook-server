from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
from pathlib import Path
import os
import base64
import hashlib

PASTA_CHAVES = Path.home() / ".pentestlink"
ARQUIVO_PRIVADA = PASTA_CHAVES / "chave_privada.pem"
ARQUIVO_PUBLICA = PASTA_CHAVES / "chave_publica.pem"
ARQUIVO_CELULAR = PASTA_CHAVES / "chave_publica_celular.pem"
ARQUIVO_TOKEN = PASTA_CHAVES / "token_sessao.txt"
ARQUIVO_CONFIRMADO = PASTA_CHAVES / "pareamento_confirmado"


def gerar_ou_carregar_chaves():
    PASTA_CHAVES.mkdir(exist_ok=True)

    if ARQUIVO_PRIVADA.exists():
        with open(ARQUIVO_PRIVADA, "rb") as f:
            chave_privada = serialization.load_pem_private_key(f.read(), password=None)
    else:
        chave_privada = Ed25519PrivateKey.generate()

        pem_privada = chave_privada.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        with open(ARQUIVO_PRIVADA, "wb") as f:
            f.write(pem_privada)
        os.chmod(ARQUIVO_PRIVADA, 0o600)

        chave_publica = chave_privada.public_key()
        pem_publica = chave_publica.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        with open(ARQUIVO_PUBLICA, "wb") as f:
            f.write(pem_publica)

    return chave_privada


def calcular_codigo_confirmacao(chave_publica_pem_1: str, chave_publica_pem_2: str) -> str:
    chaves_ordenadas = sorted([chave_publica_pem_1.strip(), chave_publica_pem_2.strip()])
    dados = "".join(chaves_ordenadas).encode("utf-8")
    hash_resultado = hashlib.sha256(dados).hexdigest()
    codigo_numerico = str(int(hash_resultado[:8], 16))[-6:].zfill(6)
    return codigo_numerico


def assinar(chave_privada, dados: bytes) -> str:
    assinatura = chave_privada.sign(dados)
    return base64.b64encode(assinatura).decode("utf-8")


def verificar_assinatura(chave_publica_pem: str, dados: bytes, assinatura_base64: str) -> bool:
    chave_publica = serialization.load_pem_public_key(chave_publica_pem.encode("utf-8"))
    assinatura = base64.b64decode(assinatura_base64)
    try:
        chave_publica.verify(assinatura, dados)
        return True
    except InvalidSignature:
        return False


def fingerprint_pem(chave_publica_pem: str) -> str:
    dados = "".join(chave_publica_pem.strip().split())
    digest = hashlib.sha256(dados.encode("utf-8")).hexdigest()
    return ":".join(digest[i:i + 4] for i in range(0, 16, 4)).upper()


def gerar_token_sessao() -> str:
    token = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8").rstrip("=")
    ARQUIVO_TOKEN.write_text(token)
    ARQUIVO_TOKEN.chmod(0o600)
    return token


def obter_token_sessao() -> str | None:
    if ARQUIVO_TOKEN.exists():
        return ARQUIVO_TOKEN.read_text().strip() or None
    return None


def salvar_chave_celular(chave_publica_pem: str) -> None:
    ARQUIVO_CELULAR.write_text(chave_publica_pem.strip() + "\n")
    ARQUIVO_CELULAR.chmod(0o600)


def obter_chave_celular() -> str | None:
    if ARQUIVO_CELULAR.exists():
        return ARQUIVO_CELULAR.read_text().strip() or None
    return None


def marcar_pareamento_confirmado() -> None:
    ARQUIVO_CONFIRMADO.write_text("1")


def pareamento_esta_confirmado() -> bool:
    return ARQUIVO_CONFIRMADO.exists()


def revogar_pareamento() -> None:
    for arquivo in (ARQUIVO_CELULAR, ARQUIVO_TOKEN, ARQUIVO_CONFIRMADO):
        if arquivo.exists():
            arquivo.unlink()


if __name__ == "__main__":
    gerar_ou_carregar_chaves()
    print("Chaves prontas.")
