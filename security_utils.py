from werkzeug.security import generate_password_hash, check_password_hash

def hash_password(password: str) -> str:
    """
    Hashes a password using PBKDF2 with a salt.
    Werkzeug's default is pbkdf2:sha256 which is highly secure.
    """
    return generate_password_hash(password)

def verify_password(stored_hash: str, provided_password: str) -> bool:
    """
    Verifies a provided password against a stored PBKDF2 hash.
    """
    return check_password_hash(stored_hash, provided_password)
