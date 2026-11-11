"""
FACTURAMI - Pytest Configuration & Test Fixtures
================================================
Garantiza el aislamiento total de las pruebas frente a la base de datos
de desarrollo/producción del repositorio ('facturami.db').
"""
import os
import tempfile
import pytest

# Definir rutas base
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DB_PATH = os.path.abspath(os.path.join(REPO_ROOT, 'facturami.db'))

# Configurar entorno de pruebas aislado antes de cargar la aplicación
TEST_TMP_DIR = tempfile.mkdtemp(prefix="facturami_pytest_")
ISOLATED_DB_PATH = os.path.join(TEST_TMP_DIR, "isolated_test_facturami.db")
ISOLATED_USERS_PATH = os.path.join(TEST_TMP_DIR, "isolated_users.json")
ISOLATED_STORAGE_PATH = os.path.join(TEST_TMP_DIR, "storage")

os.environ['FACTURAMI_DB_PATH'] = ISOLATED_DB_PATH
os.environ['FACTURAMI_USERS_FILE'] = ISOLATED_USERS_PATH
os.environ['FACTURAMI_STORAGE_PATH'] = ISOLATED_STORAGE_PATH
os.environ['JWT_SECRET_KEY'] = 'test-secret-key-fase1-authz-must-be-at-least-32-chars-long-123456'
os.environ['N8N_INTERNAL_SECRET'] = 'test-n8n-internal-secret-at-least-32-chars-long-abcdef123456789'
os.environ['N8N_WEBHOOK_URL'] = 'http://127.0.0.1:5678/webhook/test-importar'

import db as database
database.DB_PATH = ISOLATED_DB_PATH

@pytest.fixture(scope="session", autouse=True)
def verify_test_db_isolation():
    """Verifica categóricamente que ninguna prueba se ejecute contra facturami.db del repositorio."""
    current_db = os.path.abspath(database.DB_PATH)
    assert current_db != REPO_DB_PATH, (
        f"VIOLACION DE AISLAMIENTO: La base de pruebas ({current_db}) "
        f"coincide con la base del repositorio ({REPO_DB_PATH})!"
    )
    assert os.path.basename(current_db) != 'facturami.db' or current_db.startswith(tempfile.gettempdir()), (
        f"La base de datos de prueba debe residir en un directorio temporal seguro: {current_db}"
    )
    yield
