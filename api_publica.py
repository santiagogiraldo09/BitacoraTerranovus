"""
api_publica.py — API pública de Bitácora Digital (v1, sólo lectura).

Expone datos de Bitácora a herramientas externas mediante claves de API.

Principio que no se negocia: el 'empresa_id' sale SIEMPRE de la clave,
nunca de la petición. Si llega en la URL o en el cuerpo, se ignora.
"""

import functools
import hashlib
import secrets
from contextlib import contextmanager

from flask import Blueprint, request, jsonify, g

api_publica = Blueprint('api_publica', __name__, url_prefix='/api/v1')

PREFIJO = 'bitk_'

# Los seis se declaran ahora aunque la escritura llegue en la v2, para que
# el vocabulario no le cambie a un cliente que ya se integró.
ALCANCES_VALIDOS = {
    'proyectos:leer',
    'formularios:leer',
    'registros:leer',
    'archivos:leer',
    'analitica:leer',
    'registros:escribir',   # reservado, ningún endpoint lo usa todavía
}


# ──────────────────────────── Claves ────────────────────────────

def generar_clave():
    """Crea una clave nueva.

    Devuelve (token, prefijo, hash). El token se le muestra al usuario
    UNA sola vez y no se guarda en ninguna parte: en la base queda el
    hash para verificar y el prefijo para poder identificarla en la lista.
    """
    token = PREFIJO + secrets.token_urlsafe(32)
    return token, token[:13], _hash(token)


def _hash(token):
    """SHA-256 simple, a propósito.

    No se usa bcrypt ni argon2: esos son para contraseñas humanas, de poca
    entropía, donde hay que encarecer cada intento. Un token aleatorio de
    256 bits no se adivina por fuerza bruta, y un hash lento impediría
    buscar la clave por índice — tocaría recorrer todas en cada petición.
    """
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def validar_alcances(alcances):
    """Levanta ValueError si algún alcance no existe. Usar al crear claves."""
    desconocidos = set(alcances or []) - ALCANCES_VALIDOS
    if desconocidos:
        raise ValueError(f"Alcances desconocidos: {', '.join(sorted(desconocidos))}")
    if not alcances:
        raise ValueError("La clave necesita al menos un alcance")
    return list(alcances)


# ──────────────────────── Respuestas y errores ────────────────────────

def _error(estado, codigo, mensaje):
    """Error legible. Nada de códigos secos: que el cliente sepa qué le falta."""
    return jsonify({'error': {'codigo': codigo, 'mensaje': mensaje}}), estado


# ──────────────────────────── Conexión ────────────────────────────

@contextmanager
def conexion_api():
    """Conexión con el tenant fijado desde la clave, no desde la sesión.

    'db_connection' lee session['empresa_id'], que en una petición de API
    no existe y cae al valor por defecto 1. Aquí se fija explícitamente.
    """
    from app import db_connection
    with db_connection() as (conn, cursor):
        cursor.execute("SET app.empresa_id = %s", (str(g.empresa_id),))
        yield conn, cursor


# ──────────────────────────── Autenticación ────────────────────────────

def _token_de_la_peticion():
    """Acepta 'Authorization: Bearer <token>' o 'X-API-Key: <token>'."""
    cabecera = request.headers.get('Authorization', '')
    if cabecera.startswith('Bearer '):
        return cabecera[7:].strip() or None
    return request.headers.get('X-API-Key', '').strip() or None


def _buscar_clave(token):
    """Resuelve la clave. Devuelve (fila, None) o (None, respuesta_de_error).

    Se abre con el tenant en 0 porque todavía no se sabe de quién es la
    clave; 'api_keys' no tiene RLS y se filtra por hash, que es único.
    """
    from app import db_connection

    with db_connection() as (conn, cursor):
        cursor.execute("SET app.empresa_id = 0")
        cursor.execute("""
            SELECT id, empresa_id, alcances, proyectos,
                   expira_en, revocada_en,
                   (expira_en IS NOT NULL AND expira_en < now()) AS vencida
            FROM api_keys
            WHERE hash = %s
        """, (_hash(token),))
        fila = cursor.fetchone()

        if not fila:
            return None, _error(401, 'clave_invalida',
                                'La clave de API no existe o fue eliminada.')

        clave_id, empresa_id, alcances, proyectos, _exp, revocada, vencida = fila

        if revocada is not None:
            return None, _error(401, 'clave_revocada',
                                'Esta clave de API fue revocada.')
        if vencida:
            return None, _error(401, 'clave_vencida',
                                'Esta clave de API ya venció. Genere una nueva '
                                'desde Configuración.')

        # Marca de último uso, a lo sumo una vez por hora para no escribir
        # en cada petición.
        cursor.execute("""
            UPDATE api_keys
            SET ultimo_uso_en = now()
            WHERE id = %s
              AND (ultimo_uso_en IS NULL OR ultimo_uso_en < now() - interval '1 hour')
        """, (clave_id,))

        return {
            'id': clave_id,
            'empresa_id': empresa_id,
            'alcances': set(alcances or []),
            'proyectos': proyectos,     # None = todos los de la empresa
        }, None


def requiere_api_key(alcance):
    """Exige una clave válida con el alcance indicado.

    Deja en 'g': empresa_id, api_key_id y proyectos_permitidos.
    Todo el control de acceso vive aquí; ninguna ruta lo repite.
    """
    def decorador(fn):
        @functools.wraps(fn)
        def envoltura(*args, **kwargs):
            # Una clave no debe usarse desde un navegador: quedaría expuesta
            # en el código del cliente. Si hay Origin, es una página web.
            if request.headers.get('Origin'):
                return _error(403, 'origen_no_permitido',
                              'Las claves de API no pueden usarse desde un '
                              'navegador. Llame esta API desde su servidor.')

            token = _token_de_la_peticion()
            if not token:
                return _error(401, 'sin_credencial',
                              'Falta la clave de API. Envíela en la cabecera '
                              'Authorization: Bearer <clave>.')

            try:
                clave, error = _buscar_clave(token)
            except Exception:
                import traceback
                print('[API] Error resolviendo la clave:')
                traceback.print_exc()
                return _error(500, 'error_interno',
                              'No se pudo validar la clave de API.')

            if error:
                return error

            if alcance not in clave['alcances']:
                return _error(403, 'alcance_insuficiente',
                              f"La clave no tiene el alcance '{alcance}'. "
                              f"Alcances de esta clave: "
                              f"{', '.join(sorted(clave['alcances'])) or 'ninguno'}.")

            g.empresa_id = clave['empresa_id']
            g.api_key_id = clave['id']
            g.proyectos_permitidos = clave['proyectos']

            return fn(*args, **kwargs)
        return envoltura
    return decorador


# ──────────────────────────── Auditoría ────────────────────────────

@api_publica.after_request
def _registrar_uso(respuesta):
    """Deja constancia de qué clave llamó qué. La bitácora es evidencia."""
    api_key_id = getattr(g, 'api_key_id', None)
    if api_key_id is None:
        return respuesta

    try:
        from app import db_connection
        with db_connection() as (conn, cursor):
            cursor.execute("SET app.empresa_id = %s", (str(g.empresa_id),))
            cursor.execute("""
                INSERT INTO api_keys_uso
                    (api_key_id, empresa_id, ruta, metodo, estado, ip)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (api_key_id, g.empresa_id, request.path,
                  request.method, respuesta.status_code,
                  request.headers.get('X-Forwarded-For',
                                      request.remote_addr or '').split(',')[0].strip()
                  or None))
    except Exception as e:
        # La auditoría nunca puede tumbar una respuesta que ya salió bien.
        print(f'[API] No se pudo registrar el uso: {e}')

    return respuesta


# ──────────────────────── Ayudas para los endpoints ────────────────────────

def filtro_proyectos(alias='p'):
    """Fragmento SQL y parámetros para limitar a los proyectos de la clave.

    Devuelve (sql, params). Si la clave no tiene restricción, el SQL es ''.
    """
    permitidos = getattr(g, 'proyectos_permitidos', None)
    if not permitidos:
        return '', []
    return f' AND {alias}.id = ANY(%s)', [list(permitidos)]
