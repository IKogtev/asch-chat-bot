import time, os
from typing import Dict, Any

import httpx
from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError, jwk
from utils.logger import setup_logger


logger = setup_logger(
    "keycloak_auth",
    log_file="keycloak_auth.log"
)
# ============================================================
# HTTP Bearer
# ============================================================

bearer_scheme = HTTPBearer(
    auto_error=False
)


# ============================================================
# Настройки Keycloak
# ============================================================

KEYCLOAK_ISSUER = os.getenv(
    "KEYCLOAK_ISSUER",
    ""
).rstrip("/")

KEYCLOAK_AUDIENCE = os.getenv(
    "KEYCLOAK_AUDIENCE",
    "lifepoint"
)

KEYCLOAK_ALGORITHM = os.getenv(
    "KEYCLOAK_ALGORITHM",
    "RS256"
)

KEYCLOAK_JWKS_URL = (
    f"{KEYCLOAK_ISSUER}"
    "/protocol/openid-connect/certs"
)


# ============================================================
# Кэш публичных ключей Keycloak
# ============================================================

_jwks_cache: Dict[str, Any] = {}

_jwks_cache_time = 0

JWKS_CACHE_TTL = int(
    os.getenv(
        "KEYCLOAK_JWKS_CACHE_TTL",
        "3600"
    )
)


async def _get_jwks(
    force_refresh: bool = False
) -> Dict[str, Any]:
    """
    Получает публичные ключи Keycloak.

    Keycloak публикует их через JWKS endpoint.

    Ключи кэшируются, чтобы не обращаться
    к Keycloak при каждом API-запросе.
    """

    global _jwks_cache
    global _jwks_cache_time

    now = time.time()

    if (
        not force_refresh
        and _jwks_cache
        and now - _jwks_cache_time < JWKS_CACHE_TTL
    ):
        return _jwks_cache

    if not KEYCLOAK_ISSUER:
        raise RuntimeError(
            "KEYCLOAK_ISSUER is not configured"
        )

    async with httpx.AsyncClient(
        timeout=10
    ) as client:

        response = await client.get(
            KEYCLOAK_JWKS_URL
        )

        response.raise_for_status()

        data = response.json()

    _jwks_cache = {
        key["kid"]: key
        for key in data.get("keys", [])
        if key.get("kid")
    }

    _jwks_cache_time = now

    return _jwks_cache


async def _get_signing_key(kid: str):
    """
    Находит публичный ключ Keycloak
    по kid из JWT header.
    """

    keys = await _get_jwks()

    key_data = keys.get(kid)

    # Если ключ не найден — возможно,
    # Keycloak уже ротировал ключи.
    if not key_data:
        keys = await _get_jwks(
            force_refresh=True
        )

        key_data = keys.get(kid)

    if not key_data:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TOKEN",
                "message": "Signing key not found"
            }
        )

    try:
        return jwk.construct(
            key_data,
            algorithm=KEYCLOAK_ALGORITHM
        )

    except Exception:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TOKEN",
                "message": "Invalid signing key"
            }
        )


async def verify_lifepoint_jwt(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(
        bearer_scheme
    ),
) -> Dict[str, Any]:
    """
    Проверяет JWT, выданный Keycloak для LifePoint.

    Проверяем:

    1. Authorization: Bearer <JWT>
    2. JWT signature
    3. algorithm
    4. issuer
    5. audience
    6. expiration
    """

    # --------------------------------------------------------
    # 1. Authorization header
    # --------------------------------------------------------

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authorization header is required"
            },
            headers={
                "WWW-Authenticate": "Bearer"
            }
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_AUTH_SCHEME",
                "message": "Bearer authentication is required"
            },
            headers={
                "WWW-Authenticate": "Bearer"
            }
        )

    token = credentials.credentials

    # --------------------------------------------------------
    # 2. Получаем header JWT
    # --------------------------------------------------------

    try:
        header = jwt.get_unverified_header(
            token
        )

    except JWTError:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TOKEN",
                "message": "Invalid JWT header"
            },
            headers={
                "WWW-Authenticate": "Bearer"
            }
        )

    kid = header.get("kid")
    algorithm = header.get("alg")

    if not kid:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TOKEN",
                "message": "JWT kid is missing"
            }
        )

    if algorithm != KEYCLOAK_ALGORITHM:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TOKEN",
                "message": "Invalid JWT algorithm"
            }
        )

    # --------------------------------------------------------
    # 3. Получаем публичный ключ
    # --------------------------------------------------------

    signing_key = await _get_signing_key(
        kid
    )

    # --------------------------------------------------------
    # 4. Проверяем JWT
    # --------------------------------------------------------

    try:
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=[
                KEYCLOAK_ALGORITHM
            ],
            audience=KEYCLOAK_AUDIENCE,
            issuer=KEYCLOAK_ISSUER,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": False,  # Игнорируем разницу между localhost:8088 и keycloak:8080
            }
        )

    except JWTError as e:
        logger.warning(
            f"LifePoint JWT validation failed: {e}"
        )

        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TOKEN",
                "message": "Invalid or expired access token"
            },
            headers={
                "WWW-Authenticate": "Bearer"
            }
        )

    # --------------------------------------------------------
    # 5. Возвращаем claims
    # --------------------------------------------------------

    return payload