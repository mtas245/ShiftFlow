import re
import secrets
import string

from app.infrastructure.joining_code_generator import SecureJoiningCodeGenerator


def test_generator_uses_secrets_choice_for_each_code_character(monkeypatch):
    generated_characters = iter("A1B2C3D4")
    populations = []

    def choose(population):
        populations.append(population)
        return next(generated_characters)

    monkeypatch.setattr(secrets, "choice", choose)

    code = SecureJoiningCodeGenerator().generate()

    assert code == "A1B2C3D4"
    assert populations == [string.ascii_uppercase + string.digits] * 8


def test_generator_returns_eight_uppercase_alphanumeric_characters():
    generator = SecureJoiningCodeGenerator()

    for _ in range(10):
        assert re.fullmatch(r"[A-Z0-9]{8}", generator.generate())
