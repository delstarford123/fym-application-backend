import jwt
import datetime
import os

# SECURITY WARNING: In production, load this from environment variables (.env)
# Using a fallback for development; ensure this is set in production.
SECRET_KEY = os.environ.get('SECRET_KEY', 'your-highly-secure-random-production-string')
ALGORITHM = "HS256"

def generate_jwt(user_identifier: str) -> str:
    """
    Generates a secure JWT valid for 24 hours.
    The 'sub' (subject) claim holds the user identifier.
    The 'iat' (issued at) claim marks the generation time.
    The 'exp' (expiration) claim ensures the token automatically expires.
    """
    payload = {
        'sub': user_identifier,
        'iat': datetime.datetime.now(datetime.timezone.utc),
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    }
    
    # Encode the payload into a standard JWT string
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_jwt(token: str):
    """
    Decodes and validates a JWT.
    Returns the payload if valid, otherwise raises an exception.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
