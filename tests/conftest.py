"""Keep test databases inside the writable project workspace."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


@pytest.fixture
def tmp_path():
    """A per-test temporary directory that avoids restricted system temp paths."""
    workspace = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".pytest-test-", dir=workspace) as directory:
        yield Path(directory)
