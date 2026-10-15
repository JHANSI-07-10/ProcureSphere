import secrets
import string


def generate_secure_password(length: int = 14) -> str:
    """
    Generates a cryptographically secure random password meeting all requirements:
    - Minimum length 12 (default 14)
    - Includes uppercase letters, lowercase letters, digits, and special characters
    - Uses secrets module for cryptographic randomness
    - Shuffles characters to prevent predictable positional patterns
    """
    length = max(length, 14)

    uppers = string.ascii_uppercase
    lowers = string.ascii_lowercase
    digits = string.digits
    symbols = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    all_chars = uppers + lowers + digits + symbols

    # Guarantee at least 2 characters from each required character set
    password_chars = [
        secrets.choice(uppers),
        secrets.choice(uppers),
        secrets.choice(lowers),
        secrets.choice(lowers),
        secrets.choice(digits),
        secrets.choice(digits),
        secrets.choice(symbols),
        secrets.choice(symbols),
    ]

    # Fill remaining characters from combined pool
    for _ in range(length - len(password_chars)):
        password_chars.append(secrets.choice(all_chars))

    # Cryptographically secure Fisher-Yates shuffle
    shuffled = []
    pool = list(password_chars)
    while pool:
        idx = secrets.randbelow(len(pool))
        shuffled.append(pool.pop(idx))

    return "".join(shuffled)
