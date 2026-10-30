"""
FACTURAMI - Respuestas API estandarizadas y manejo de errores.
"""
from flask import jsonify
import traceback


# ══════════════════════════════════════════════════════════════════════════
#  Respuestas estandar
# ══════════════════════════════════════════════════════════════════════════

def success(data=None, message=None, status=200):
    """Respuesta exitosa estandar."""
    body = {'ok': True}
    if message:
        body['message'] = message
    if data is not None:
        if isinstance(data, dict):
            body.update(data)
        else:
            body['data'] = data
    return jsonify(body), status


def error(message, status=400, code=None, **extra):
    """Respuesta de error estandar."""
    body = {'ok': False, 'error': message}
    if code:
        body['code'] = code
    body.update(extra)
    return jsonify(body), status


def created(data=None, message=None):
    """Atajo para 201 Created."""
    return success(data, message, 201)


def not_found(message='Recurso no encontrado'):
    """Atajo para 404."""
    return error(message, 404)


def conflict(message, **extra):
    """Atajo para 409 Conflict."""
    return error(message, 409, **extra)


def forbidden(message='Acceso denegado'):
    """Atajo para 403."""
    return error(message, 403)


def server_error(message='Error interno del servidor'):
    """Atajo para 500."""
    return error(message, 500)


# ══════════════════════════════════════════════════════════════════════════
#  Decorador de manejo de errores
# ══════════════════════════════════════════════════════════════════════════

def handle_errors(f):
    """Decorador que envuelve un endpoint en try/except con respuesta estandar."""
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValueError as e:
            print(f"[ERROR] ValueError en {f.__name__}: {e}")
            return error(str(e), 400)
        except Exception as e:
            print(f"[ERROR] Excepcion en {f.__name__}: {e}")
            traceback.print_exc()
            return server_error(str(e))

    return wrapper


# ══════════════════════════════════════════════════════════════════════════
#  Validacion de datos
# ══════════════════════════════════════════════════════════════════════════

class ValidationError(Exception):
    """Error de validacion de datos."""
    def __init__(self, message, field=None):
        self.message = message
        self.field = field
        super().__init__(message)


def validate(data, rules):
    """
    Valida un diccionario contra reglas.
    
    rules = {
        'campo': {'required': True, 'type': str, 'max_len': 100},
        'monto': {'required': False, 'type': (int, float), 'min': 0},
    }
    
    Retorna el dict con valores limpiados, o lanza ValidationError.
    """
    if not isinstance(data, dict):
        raise ValidationError('Se esperaba un objeto JSON')

    cleaned = {}
    errors = []

    for field, rule in rules.items():
        value = data.get(field)

        # Requerido
        if rule.get('required') and (value is None or value == ''):
            errors.append(f"'{field}' es obligatorio")
            continue

        # Si no existe y no es requerido, usar default
        if value is None or value == '':
            cleaned[field] = rule.get('default', '')
            continue

        # Tipo
        expected_type = rule.get('type')
        if expected_type and not isinstance(value, expected_type):
            # Intentar castear
            try:
                if expected_type in (int, float) and isinstance(value, str):
                    value = expected_type(value.replace(',', '.').replace(' ', ''))
                elif expected_type == str:
                    value = str(value)
                else:
                    errors.append(f"'{field}' debe ser de tipo {expected_type.__name__}")
                    continue
            except (ValueError, TypeError):
                errors.append(f"'{field}' tiene un formato invalido")
                continue

        # Longitud maxima (strings)
        if isinstance(value, str) and rule.get('max_len') and len(value) > rule['max_len']:
            errors.append(f"'{field}' excede {rule['max_len']} caracteres")
            continue

        # Minimo (numeros)
        if isinstance(value, (int, float)) and 'min' in rule and value < rule['min']:
            errors.append(f"'{field}' debe ser >= {rule['min']}")
            continue

        # Valores permitidos
        if 'choices' in rule and value not in rule['choices']:
            errors.append(f"'{field}' debe ser uno de: {rule['choices']}")
            continue

        # Strip strings
        if isinstance(value, str):
            value = value.strip()

        cleaned[field] = value

    if errors:
        raise ValidationError('; '.join(errors))

    return cleaned
