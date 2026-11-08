"""
FACTURAMI - Punto de entrada principal.
=========================================
Arquitectura modular:
  app.py          -> App factory, config, startup
  db.py           -> Capa de base de datos SQLite
  errors.py       -> Respuestas estandar y manejo de errores
  schemas.py      -> Schemas de validacion
  routes/
    auth.py       -> /api/login, /api/auth/*
    facturas.py   -> /api/facturas/*
    sync.py       -> /api/sync/*, /api/config/*
  services/
    auth_service.py    -> Logica de autenticacion
    queue_service.py   -> Logica de cola de facturas
    sync_service.py    -> Logica de sincronizacion
    upload_service.py  -> Logica de upload PDF -> n8n
"""
import os
from datetime import timedelta

from flask import Flask, jsonify, send_file, make_response
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from dotenv import load_dotenv

# ── Cargar variables de entorno ────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# ── Inicializar base de datos ──────────────────────────────────────────────
import db as database


def _require_strong_secrets():
    """Se niega a arrancar si los secretos faltan o son debiles."""
    for var in ('JWT_SECRET_KEY', 'N8N_INTERNAL_SECRET'):
        v = os.environ.get(var, '')
        if len(v) < 32 or v.upper().startswith('CAMBIAR'):
            raise RuntimeError(f'{var} no esta configurada o es insegura (minimo 32 caracteres)')


def create_app():
    """App factory: crea y configura la aplicacion Flask."""
    _require_strong_secrets()
    app = Flask(__name__)

    # ── Configuracion ──────────────────────────────────────────────────
    app.config['JWT_SECRET_KEY'] = os.environ['JWT_SECRET_KEY']
    app.config['JWT_ALGORITHM'] = 'HS256'
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=12)
    app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024  # 15 MB por peticion

    # ── CORS ───────────────────────────────────────────────────────────
    cors_origins = os.environ.get('CORS_ORIGINS', '*')
    if cors_origins == '*' or not cors_origins.strip():
        CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    else:
        origins = [o.strip() for o in cors_origins.split(',') if o.strip()]
        CORS(app, origins=origins, supports_credentials=True)

    # ── JWT ─────────────────────────────────────────────────────────────
    jwt = JWTManager(app)

    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({'ok': False, 'error': 'Token expirado', 'code': 'TOKEN_EXPIRED'}), 401

    @jwt.invalid_token_loader
    def invalid_token_callback(err):
        return jsonify({'ok': False, 'error': 'Token invalido', 'code': 'TOKEN_INVALID'}), 401

    @jwt.unauthorized_loader
    def missing_token_callback(err):
        return jsonify({'ok': False, 'error': 'Token requerido', 'code': 'TOKEN_MISSING'}), 401

    # ── Registrar Blueprints API ───────────────────────────────────────
    from routes.auth import auth_bp
    from routes.facturas import facturas_bp
    from routes.sync import sync_bp
    from routes.admin import admin_bp
    from routes.contabilidad import contabilidad_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(facturas_bp)
    app.register_blueprint(sync_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(contabilidad_bp)

    # ── Encabezados Globales de Seguridad ───────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Bloquear grabacion de pantalla no autorizada via APIs de navegador
        response.headers['Permissions-Policy'] = 'display-capture=(), camera=(), microphone=(), geolocation=()'
        return response

    # ── Rutas Web / PWA Frontend ───────────────────────────────────────
    @app.route('/')
    @app.route('/index.html')
    @app.route('/inicio')
    @app.route('/landing')
    @app.route('/Preguntas Frecuentes')
    @app.route('/Preguntas%20Frecuentes')
    @app.route('/preguntas-frecuentes')
    @app.route('/preguntas_frecuentes')
    @app.route('/preguntas')
    @app.route('/faq')
    def serve_landing():
        landing_path = os.path.join(BASE_DIR, 'landing.html')
        if os.path.exists(landing_path):
            resp = make_response(send_file(landing_path))
            resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers['Expires'] = '0'
            return resp
        return serve_app()

    @app.route('/app')
    @app.route('/login')
    @app.route('/sistema')
    @app.route('/FACTURAMI.html')
    def serve_app():
        html_path = os.path.join(BASE_DIR, 'FACTURAMI.html')
        if os.path.exists(html_path):
            resp = make_response(send_file(html_path, conditional=False))
            resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0, post-check=0, pre-check=0'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers['Expires'] = '0'
            return resp
        return jsonify({'ok': False, 'error': 'FACTURAMI.html no encontrado'}), 404

    @app.route('/manifest.json')
    def serve_manifest():
        manifest_path = os.path.join(BASE_DIR, 'manifest.json')
        if os.path.exists(manifest_path):
            return send_file(manifest_path, mimetype='application/manifest+json')
        return jsonify({'ok': False, 'error': 'manifest.json no encontrado'}), 404

    @app.route('/favicon.ico')
    def serve_favicon():
        ico_path = os.path.join(BASE_DIR, 'facturami.ico')
        if os.path.exists(ico_path):
            return send_file(ico_path, mimetype='image/x-icon')
        return ('', 204)

    @app.route('/icon-192.png')
    def serve_icon_192():
        icon_path = os.path.join(BASE_DIR, 'icon-192.png')
        if os.path.exists(icon_path):
            return send_file(icon_path, mimetype='image/png')
        return ('', 404)

    @app.route('/icon-512.png')
    def serve_icon_512():
        icon_path = os.path.join(BASE_DIR, 'icon-512.png')
        if os.path.exists(icon_path):
            return send_file(icon_path, mimetype='image/png')
        return ('', 404)

    @app.route('/<filename>')
    def serve_static_root_files(filename):
        # Whitelist estricta de imagenes publicas en raiz. NUNCA sirve subdirectorios (como storage/)
        PUBLIC_ROOT_FILES = {
            'premium_abstract_background_1774919744694.png',
            'sky_clouds_background_1774918448207.png',
            'qr_facturami_movil.png',
            'Declaracion D150 COMPRAS.png',
            'Declaracion D150 VENTASS.png',
            'Declaracion regimen simplificado.png',
        }
        if filename in PUBLIC_ROOT_FILES:
            file_path = os.path.join(BASE_DIR, filename)
            if os.path.isfile(file_path):
                return send_file(file_path)
        return jsonify({'ok': False, 'error': 'Archivo no encontrado'}), 404

    # ── Error handlers globales ────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'ok': False, 'error': 'Endpoint no encontrado'}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({'ok': False, 'error': 'Metodo no permitido'}), 405

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({'ok': False, 'error': 'Archivo demasiado grande (maximo 15 MB)'}), 413

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({'ok': False, 'error': 'Error interno del servidor'}), 500

    return app


# ══════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════

app = create_app()

if __name__ == '__main__':
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001))
    db_size = os.path.getsize(database.DB_PATH) / 1024 if os.path.exists(database.DB_PATH) else 0

    print("=" * 55)
    print("  FACTURAMI v4.0 - Servidor Cloud / Local")
    print("-" * 55)
    print(f"  Host:    {host}")
    print(f"  Puerto:  {port}")
    print(f"  DB:      {database.DB_PATH} ({db_size:.1f} KB)")
    print("  JWT:     [OK] (secreto validado al arrancar)")
    print(f"  Gemini:  {'[OK]' if os.environ.get('GEMINI_API_KEY') else '[--]'}")
    print("-" * 55)
    print("  Rutas Activas:")
    print("    /                   GET    Landing Page (Planes y Precios)")
    print("    /app, /login        GET    FACTURAMI Web App / Login")
    print("    /manifest.json      GET    PWA Manifest")
    print("    /api/login          POST   Auth")
    print("    /api/auth/verify    GET    Auth")
    print("    /api/facturas/*     CRUD   Facturas")
    print("    /api/sync/*         GET/POST Sync")
    print("    /api/config/public  GET    Config")
    print("=" * 55)
    app.run(host=host, port=port, debug=False)

