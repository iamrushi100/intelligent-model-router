import pytest

from app.schemas import Sensitivity
from app.sensitivity import detect_sensitivity, effective_sensitivity


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ordinary public text", Sensitivity.PUBLIC),
        ("Contact person@example.com", Sensitivity.INTERNAL),
        ("Call +1 (415) 555-0132", Sensitivity.INTERNAL),
        ("Card 4111 1111 1111 1111", Sensitivity.RESTRICTED),
        ("Aadhaar 1234 5678 9012", Sensitivity.RESTRICTED),
        ("PAN ABCDE1234F", Sensitivity.RESTRICTED),
        ("api_key=abcdefghijklmnopqrstuvwxyz123456", Sensitivity.RESTRICTED),
        ("sk-abcdefghijklmnopqrstuvwxyz123456", Sensitivity.RESTRICTED),
    ],
)
def test_detect_common_sensitivity_patterns(text: str, expected: Sensitivity) -> None:
    assert detect_sensitivity(text) is expected


@pytest.mark.parametrize(
    ("declared", "detected", "expected"),
    [
        (Sensitivity.PUBLIC, Sensitivity.PUBLIC, Sensitivity.PUBLIC),
        (Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.INTERNAL),
        (Sensitivity.CONFIDENTIAL, Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL),
        (Sensitivity.RESTRICTED, Sensitivity.PUBLIC, Sensitivity.RESTRICTED),
    ],
)
def test_effective_sensitivity_uses_stricter_level(
    declared: Sensitivity, detected: Sensitivity, expected: Sensitivity
) -> None:
    assert effective_sensitivity(declared, detected) is expected
