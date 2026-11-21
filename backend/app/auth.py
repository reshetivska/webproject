import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import ADMIN_USERS, TOKEN_TTL_HOURS
from app.db import AuthToken, User, get_db, utcnow


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return salt + "$" + digest.hex()


def verify_password(password, stored):
    salt, _, digest_hex = stored.partition("$")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_token(db, user_id):
    token = secrets.token_hex(32)
    db.add(AuthToken(token=token, user_id=user_id))
    db.commit()
    return token


def get_current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Потрібна авторизація")
    row = db.get(AuthToken, authorization[7:])
    if row is None or row.created_at < utcnow() - timedelta(hours=TOKEN_TTL_HOURS):
        raise HTTPException(401, "Сесія недійсна, увійдіть знову")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "Користувача не знайдено")
    return user


def get_admin(user: User = Depends(get_current_user)):
    if user.username not in ADMIN_USERS:
        raise HTTPException(403, "Тільки для адміністратора")
    return user
