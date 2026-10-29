import secrets
import string


_JOINING_CODE_ALPHABET = string.ascii_uppercase + string.digits


class SecureJoiningCodeGenerator:
    def generate(self) -> str:
        return "".join(secrets.choice(_JOINING_CODE_ALPHABET) for _ in range(8))
