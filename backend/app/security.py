import hashlib, hmac, os, datetime as dt
import jwt
from .config import settings


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def make_token(email: str, hours=None) -> str:
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours or settings.token_hours)
    return jwt.encode({"sub": email, "exp": exp}, settings.secret_key, algorithm="HS256")


def read_token(token: str):
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])["sub"]
    except Exception:
        return None
