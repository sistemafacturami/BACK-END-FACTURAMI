"""
Tests de navegador End-to-End con Playwright para FACTURAMI.
==============================================================
Valida el ciclo de vida del front-end, almacenamiento local, namespacing multi-cuenta,
manejo de cuota llena, recuperacion ante 401 y exclusion de clientes ante 403.
Todos los datos son 100% ficticios. No conecta a servidores remotos ni produccion.
"""
import os
import json
import pytest
from urllib.parse import unquote
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(BASE_DIR, 'FACTURAMI.html').replace('\\', '/')
PAGE_URL = f"file:///{HTML_PATH}"


@pytest.fixture(scope="module")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        yield context
        context.close()
        browser.close()


def setup_mock_routes(page, push_handler=None):
    """Intercepta llamadas a la API del backend con respuestas mockeadas."""
    def handle_login(route):
        body = json.loads(route.request.post_data or '{}')
        username = body.get('username', 'usuario_test')
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "ok": True,
                "token": "mock.jwt.token",
                "user": {
                    "username": username,
                    "nombre": f"Contador {username}",
                    "role": "contador"
                }
            })
        )

    def handle_sync_pull(route):
        url = unquote(route.request.url)
        cuenta_id = ""
        if "cuenta_id=" in url:
            cuenta_id = url.split("cuenta_id=")[1].split("&")[0].strip()

        # Si el servidor responde para contador_a, devuelve su cliente mock
        server_clients = []
        if cuenta_id == "contador_a":
            server_clients = [{'id': 'CLI-001', 'name': 'Empresa Ficticia A', 'type': 'PJ'}]

        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "ok": True,
                "data": {
                    "f_clients": server_clients,
                    "f_theme": "dark"
                }
            })
        )

    def default_push(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "saved": True})
        )

    page.route("**/api/login", handle_login)
    page.route("**/api/sync/pull*", handle_sync_pull)
    if push_handler:
        page.route("**/api/sync/push", push_handler)
    else:
        page.route("**/api/sync/push", default_push)


def perform_login(page, username, password="clave_ficticia_123"):
    """Llena el formulario de login y ejecuta doLogin()."""
    page.fill("#u_name", username)
    page.fill("#u_pass", password)
    page.click(".btn-login-primary")
    page.wait_for_timeout(600)


def test_escenario_a_cerrar_pestana_reabrir_misma_cuenta(browser_context):
    """
    (a) Cerrar pestaña sin logout, volver a entrar con la misma cuenta:
    no se pierde nada y no hay prompt de confirmación.
    """
    page = browser_context.new_page()
    setup_mock_routes(page)
    page.goto(PAGE_URL)

    # 1. Preparar espacio activo como 'contador_a' con datos locales
    datos_cliente = [{'id': 'CLI-001', 'name': 'Empresa Ficticia A', 'type': 'PJ'}]
    page.evaluate("""({clients}) => {
        localStorage.setItem('f_clients', JSON.stringify(clients));
        localStorage.setItem('f_data_CLI-001', JSON.stringify({asientos: [{id: 'AST-1', monto: 100}]}));
        localStorage.setItem('fm:active_owner', 'contador_a');
    }""", {'clients': datos_cliente})

    # Cerrar pestaña sin llamar doLogout()
    page.close()

    # 2. Abrir nueva pestaña en el mismo contexto
    new_page = browser_context.new_page()
    setup_mock_routes(new_page)
    dialogs_shown = []
    new_page.on("dialog", lambda dialog: (dialogs_shown.append(dialog.message), dialog.accept()))

    new_page.goto(PAGE_URL)

    # Realizar login con la misma cuenta 'contador_a'
    perform_login(new_page, "contador_a")

    # Verificaciones:
    # - No se mostraron ventanas de confirmación / prompts
    assert len(dialogs_shown) == 0, f"No debió aparecer ningún prompt, aparecieron: {dialogs_shown}"

    # - Los datos del cliente no se perdieron
    clients_in_storage = new_page.evaluate("() => JSON.parse(localStorage.getItem('f_clients') || '[]')")
    assert len(clients_in_storage) == 1
    assert clients_in_storage[0]['id'] == 'CLI-001'
    assert clients_in_storage[0]['name'] == 'Empresa Ficticia A'

    owner = new_page.evaluate("() => localStorage.getItem('fm:active_owner')")
    assert owner == 'contador_a'
    new_page.close()


def test_escenario_b_cerrar_pestana_entra_otra_cuenta_no_ve_datos_anteriores(browser_context):
    """
    (b) Cerrar pestaña sin logout, entra otra cuenta:
    no ve datos de la anterior (se guardan automáticamente en su namespace sin preguntar).
    """
    page = browser_context.new_page()
    setup_mock_routes(page)
    page.goto(PAGE_URL)

    # 1. Preparar datos de contador_a en workspace activo
    page.evaluate("""() => {
        localStorage.setItem('f_clients', JSON.stringify([{'id': 'CLI-PRIVADO-A', 'name': 'Confidencial A'}]));
        localStorage.setItem('f_data_CLI-PRIVADO-A', JSON.stringify({facturas: [1, 2]}));
        localStorage.setItem('fm:active_owner', 'contador_a');
    }""")
    page.close()

    # 2. Reabrir pestaña e ingresar como 'contador_b'
    new_page = browser_context.new_page()
    setup_mock_routes(new_page)
    dialogs_shown = []
    new_page.on("dialog", lambda dialog: (dialogs_shown.append(dialog.message), dialog.accept()))

    new_page.goto(PAGE_URL)
    perform_login(new_page, "contador_b")

    # No debe haber preguntado confirmación porque el dueño era conocido
    assert len(dialogs_shown) == 0, f"No debió pedir confirmación, apareció: {dialogs_shown}"

    # contador_b no debe ver en f_clients los datos privados de contador_a
    active_clients = new_page.evaluate("() => JSON.parse(localStorage.getItem('f_clients') || '[]')")
    active_client_ids = [c.get('id') for c in active_clients]
    assert 'CLI-PRIVADO-A' not in active_client_ids

    # Los datos de contador_a deben estar preservados en fm:contador_a:clients
    a_namespaced = new_page.evaluate("() => localStorage.getItem('fm:contador_a:clients')")
    assert a_namespaced is not None
    assert 'CLI-PRIVADO-A' in a_namespaced

    # El dueño activo ahora es contador_b
    owner = new_page.evaluate("() => localStorage.getItem('fm:active_owner')")
    assert owner == 'contador_b'
    new_page.close()


def test_escenario_c_localstorage_lleno_logout_no_borra_y_avisa(browser_context):
    """
    (c) localStorage lleno: logout no borra datos y avisa de error de cuota.
    """
    page = browser_context.new_page()
    setup_mock_routes(page)
    page.goto(PAGE_URL)

    # Preparar sesión activa con datos
    page.evaluate("""() => {
        localStorage.setItem('f_clients', JSON.stringify([{'id': 'CLI-CRITICO', 'name': 'Datos Intactos'}]));
        localStorage.setItem('f_data_CLI-CRITICO', JSON.stringify({saldo: 50000}));
        localStorage.setItem('fm:active_owner', 'contador_a');
        sessionStorage.setItem('f_logged_in', 'true');
        sessionStorage.setItem('f_user', JSON.stringify({username: 'contador_a'}));
    }""")

    # Simular que localStorage.setItem lanza excepción de QuotaExceededError
    page.evaluate("""() => {
        const origSetItem = localStorage.setItem.bind(localStorage);
        window.localStorage.setItem = function(k, v) {
            if (k.startsWith('fm:contador_a:')) {
                const err = new DOMException('QuotaExceededError: LocalStorage limit reached', 'QuotaExceededError');
                throw err;
            }
            return origSetItem(k, v);
        };
    }""")

    # Ejecutar doLogout()
    page.evaluate("() => doLogout()")
    page.wait_for_timeout(300)

    # Verificación: f_clients y f_data_ NO fueron borrados
    clients_after = page.evaluate("() => localStorage.getItem('f_clients')")
    data_after = page.evaluate("() => localStorage.getItem('f_data_CLI-CRITICO')")
    assert clients_after is not None
    assert 'CLI-CRITICO' in clients_after
    assert data_after is not None

    page.close()


def test_escenario_d_cambio_de_cuenta_tras_401(browser_context):
    """
    (d) Cambio de cuenta tras 401 en la misma pestaña:
    preserva datos de la primera cuenta y restaura la segunda.
    """
    page = browser_context.new_page()
    setup_mock_routes(page)
    page.goto(PAGE_URL)

    # Iniciar sesión con contador_1
    perform_login(page, "contador_1")

    # Agregar cliente para contador_1
    page.evaluate("""() => {
        localStorage.setItem('f_clients', JSON.stringify([{'id': 'CLI-C1', 'name': 'Cliente de Cuenta 1'}]));
        localStorage.setItem('f_data_CLI-C1', JSON.stringify({notas: 'privado'}));
        localStorage.setItem('fm:active_owner', 'contador_1');
    }""")

    # Simular expiración de sesión (401 en pushToBackend) y retorno a vista de login
    page.evaluate("""() => {
        sessionStorage.removeItem('f_token');
        document.querySelectorAll('.auth-view, #app-wrap, #client-list-view').forEach(el => el.style.display = 'none');
        const lv = document.getElementById('login-view');
        lv.style.display = 'flex';
        lv.style.opacity = '1';
    }""")
    page.wait_for_timeout(300)

    # Login con contador_2 en la misma pestaña
    perform_login(page, "contador_2")

    # Datos de contador_1 deben estar resguardados en fm:contador_1:clients
    c1_saved = page.evaluate("() => localStorage.getItem('fm:contador_1:clients')")
    assert c1_saved is not None
    assert 'CLI-C1' in c1_saved

    # Workspace activo pertenece a contador_2
    owner = page.evaluate("() => localStorage.getItem('fm:active_owner')")
    assert owner == 'contador_2'

    page.close()


def test_escenario_e_403_cedula_ajena_resto_sigue_sincronizando(browser_context):
    """
    (e) 403 por cédula ajena: el cliente ofensor se bloquea localmente (_sync_blocked),
    se conserva en backup y el resto sigue sincronizando.
    """
    page = browser_context.new_page()

    push_attempts = []

    def mock_push_handler(route):
        body = json.loads(route.request.post_data or '{}')
        clients = body.get('f_clients', [])
        client_ids = [c['id'] for c in clients]
        push_attempts.append(client_ids)

        if 'CLI-AJENO-999' in client_ids:
            # Backend rechaza con 403 y devuelve cliente_id ofensor
            route.fulfill(
                status=403,
                content_type="application/json",
                body=json.dumps({
                    "ok": False,
                    "error": "Acceso denegado a cliente no perteneciente a la cuenta",
                    "cliente_id": "CLI-AJENO-999"
                })
            )
        else:
            # Segundo intento (sin el ofensor): éxito 200
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "saved": True})
            )

    setup_mock_routes(page, push_handler=mock_push_handler)
    page.goto(PAGE_URL)

    # Iniciar sesión
    perform_login(page, "contador_a")

    # Configurar clientes: uno válido y uno ajeno
    page.evaluate("""() => {
        const clients = [
            {'id': 'CLI-VALIDO-100', 'name': 'Mi Cliente Valido'},
            {'id': 'CLI-AJENO-999', 'name': 'Cliente En Conflicto'}
        ];
        localStorage.setItem('f_clients', JSON.stringify(clients));
        localStorage.setItem('f_data_CLI-VALIDO-100', JSON.stringify({}));
        localStorage.setItem('f_data_CLI-AJENO-999', JSON.stringify({}));
    }""")

    # Disparar pushToBackend(silent=false)
    page.evaluate("() => pushToBackend(false)")
    page.wait_for_timeout(1500)

    # Verificaciones:
    # 1. Hubo dos intentos de push
    assert len(push_attempts) >= 2, f"Se esperaban al menos 2 intentos, hubo {len(push_attempts)}: {push_attempts}"
    assert 'CLI-AJENO-999' in push_attempts[0]
    assert 'CLI-AJENO-999' not in push_attempts[1]
    assert 'CLI-VALIDO-100' in push_attempts[1]

    # 2. El cliente ofensor fue marcado con _sync_blocked
    clients_after = page.evaluate("() => JSON.parse(localStorage.getItem('f_clients') || '[]')")
    offender = next((c for c in clients_after if c['id'] == 'CLI-AJENO-999'), None)
    assert offender is not None
    assert offender.get('_sync_blocked') is True

    # 3. Se generó respaldo de seguridad 'push_403_conflict'
    backups = page.evaluate("""() => {
        return Object.keys(localStorage)
            .filter(k => k.startsWith('fm_backup_'))
            .map(k => JSON.parse(localStorage.getItem(k) || '{}'));
    }""")
    assert any(b.get('reason') == 'push_403_conflict' for b in backups)

    page.close()


def test_escenario_f_pendientes_y_push_fallido_datos_locales_no_cambian(browser_context):
    """
    (f) Con fm_pending_sync y push fallido, syncFromBackend NO hace el pull
    y los datos locales no cambian.
    """
    page = browser_context.new_page()

    pull_called = []
    def mock_pull(route):
        pull_called.append(True)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "ok": True,
                "data": {
                    "f_clients": [{"id": "CLI-SERVER-99", "name": "Sobrescrito del Servidor"}],
                    "f_theme": "dark"
                }
            })
        )

    def mock_push_failing(route):
        route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": "Error interno del servidor"})
        )

    setup_mock_routes(page, push_handler=mock_push_failing)
    page.route("**/api/sync/pull*", mock_pull)
    page.goto(PAGE_URL)

    # Iniciar sesión
    perform_login(page, "contador_a")

    # Preparar datos locales pendientes
    page.evaluate("""() => {
        const local = [{'id': 'CLI-LOCAL-1', 'name': 'Cliente Local Intacto'}];
        localStorage.setItem('f_clients', JSON.stringify(local));
        localStorage.setItem('f_data_CLI-LOCAL-1', JSON.stringify({notas: 'No debe perderse'}));
        localStorage.setItem('fm_pending_sync_contador_a', '1');
    }""")

    # Limpiar llamadas del pull del login inicial para evaluar estrictamente syncFromBackend con pendientes
    pull_called.clear()

    # Ejecutar syncFromBackend con pendientes activos y push fallido
    result = page.evaluate("() => syncFromBackend('contador_a', false)")
    page.wait_for_timeout(800)

    # 1. El pull NO debió ser ejecutado porque el push falló
    assert len(pull_called) == 0, "No debió ejecutar pull si el push de pendientes falló"
    assert result is False

    # 2. Los datos locales NO cambiaron
    clients_after = page.evaluate("() => JSON.parse(localStorage.getItem('f_clients') || '[]')")
    assert len(clients_after) == 1
    assert clients_after[0]['id'] == 'CLI-LOCAL-1'
    assert clients_after[0]['name'] == 'Cliente Local Intacto'

    data_after = page.evaluate("() => localStorage.getItem('f_data_CLI-LOCAL-1')")
    assert 'No debe perderse' in data_after

    # 3. La bandera de pendientes sigue en '1'
    assert page.evaluate("() => localStorage.getItem('fm_pending_sync_contador_a')") == '1'

    page.close()


def test_rotacion_respaldos_10_pulls_no_expulsan_pre_migracion(browser_context):
    """
    (2) Retención por motivo: 10 pulls sucesivos (pre_pull_sync) NO expulsan
    un respaldo protegido pre_migracion.
    """
    page = browser_context.new_page()
    page.goto(PAGE_URL)

    # Crear manualmente un respaldo de motivo protegido 'pre_migracion'
    page.evaluate("""() => {
        localStorage.clear();
        const bPreMigracion = {
            timestamp: '2026-01-01T00:00:00.000Z',
            reason: 'pre_migracion',
            cuenta_id: 'contador_a',
            f_clients: [{'id': 'CLI-ANTIGUO', 'name': 'Cliente Protegido'}],
            data: {}
        };
        localStorage.setItem('fm_backup_2026-01-01T00-00-00-000Z', JSON.stringify(bPreMigracion));

        // Simular 10 operaciones de pull que generan respaldos de rutina pre_pull_sync
        for (let i = 1; i <= 10; i++) {
            const pad = String(i).padStart(2, '0');
            const bRoutine = {
                timestamp: `2026-02-01T00:00:${pad}.000Z`,
                reason: 'pre_pull_sync',
                cuenta_id: 'contador_a',
                f_clients: [{'id': `CLI-PULL-${i}`, 'name': `Pull ${i}`}],
                data: {}
            };
            localStorage.setItem(`fm_backup_2026-02-01T00-00-${pad}-000Z`, JSON.stringify(bRoutine));
            rotateLocalBackups();
        }
    }""")

    # Verificar que el respaldo pre_migracion sigue existiendo intacto
    pre_mig = page.evaluate("() => localStorage.getItem('fm_backup_2026-01-01T00-00-00-000Z')")
    assert pre_mig is not None, "El respaldo protegido pre_migracion NO debe haber sido expulsado por los 10 pulls"

    # Verificar que los respaldos rutinarios se limitaron al máximo de rutina (2)
    routine_backups = page.evaluate("""() => {
        return Object.keys(localStorage)
            .filter(k => k.startsWith('fm_backup_'))
            .map(k => JSON.parse(localStorage.getItem(k) || '{}'))
            .filter(b => b.reason === 'pre_pull_sync');
    }""")
    assert len(routine_backups) <= 2, f"Los respaldos pre_pull_sync debieron rotar a máximo 2, pero hay {len(routine_backups)}"

    page.close()
