import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci")
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def app_ctx():
    from app import create_app

    app = create_app(test_config={"TESTING": True})
    with app.app_context():
        yield app
