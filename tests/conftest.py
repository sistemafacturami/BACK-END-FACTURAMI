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
    """Inicializa la BD temporal y verifica categóricamente que ninguna prueba se ejecute contra facturami.db, users.json ni storage del repositorio."""
    repo_users = os.path.abspath(os.path.join(REPO_ROOT, 'users.json'))
    repo_storage = os.path.abspath(os.path.join(REPO_ROOT, 'storage'))

    current_db = os.path.abspath(database.DB_PATH)
    current_users = os.path.abspath(os.environ.get('FACTURAMI_USERS_FILE', ''))
    current_storage = os.path.abspath(os.environ.get('FACTURAMI_STORAGE_PATH', ''))

    assert current_db != REPO_DB_PATH, (
        f"VIOLACION DE AISLAMIENTO: La base de pruebas ({current_db}) "
        f"coincide con la base del repositorio ({REPO_DB_PATH})!"
    )
    assert os.path.basename(current_db) != 'facturami.db' or current_db.startswith(tempfile.gettempdir()), (
        f"La base de datos de prueba debe residir en un directorio temporal seguro: {current_db}"
    )

    assert current_users != repo_users, (
        f"VIOLACION DE AISLAMIENTO: users.json de pruebas ({current_users}) coincide con el del repo ({repo_users})!"
    )
    assert current_storage != repo_storage, (
        f"VIOLACION DE AISLAMIENTO: storage de pruebas ({current_storage}) coincide con el del repo ({repo_storage})!"
    )

    # Inicializar tablas e índices en la base temporal
    os.makedirs(ISOLATED_STORAGE_PATH, exist_ok=True)
    database.init_db(ISOLATED_DB_PATH)
    from scripts.migrate_v2_niif import migrate
    migrate(ISOLATED_DB_PATH)
    yield

