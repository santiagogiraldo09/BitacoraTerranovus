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
from flask import Blueprint, request, jsonify, g, session

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
# 120 por minuto alcanza de sobra para un tablero que refresca cada hora
# y frena a una integración en bucle. Una clave puede tener el suyo.
LIMITE_POR_MINUTO = 120


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
                   (expira_en IS NOT NULL AND expira_en < now()) AS vencida,
                   limite_por_minuto
            FROM api_keys
            WHERE hash = %s
        """, (_hash(token),))
        fila = cursor.fetchone()

        if not fila:
            return None, _error(401, 'clave_invalida',
                                'La clave de API no existe o fue eliminada.')

        (clave_id, empresa_id, alcances, proyectos,
         _exp, revocada, vencida, limite) = fila
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

        # El límite se mide aquí, con la conexión ya abierta, para no
        # pagar una segunda ida a la base en cada petición.
        cursor.execute("""
            SELECT count(*) FROM api_keys_uso
            WHERE api_key_id = %s AND creado_en > now() - interval '1 minute'
        """, (clave_id,))
        usadas = cursor.fetchone()[0]
        tope = limite or LIMITE_POR_MINUTO

        if usadas >= tope:
            respuesta = _error(429, 'limite_excedido',
                               f'Superaste el límite de {tope} peticiones por '
                               f'minuto para esta clave. Reintenta en un momento.')
            respuesta[0].headers['Retry-After'] = '60'
            return None, respuesta

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


@api_publica.before_request
def _sin_cookie_de_sesion():
    """Una API no devuelve cookies de sesión.

    Algún before_request global marca la sesión como permanente, y Flask
    la reenvía en cada respuesta. Aquí se deshace para este blueprint: la
    cookie no sirve de nada a un cliente de API y el 'Vary: Cookie' que
    arrastra estorba a cualquier caché intermedia.
    """
    from flask import session
    session.permanent = False
    session.modified = False

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

# ──────────────────────────── Paginación ────────────────────────────

LIMITE_POR_DEFECTO = 50
LIMITE_MAXIMO = 200


def _limite():
    """Tamaño de página pedido, acotado para que nadie pida la base entera."""
    try:
        n = int(request.args.get('limite', LIMITE_POR_DEFECTO))
    except (TypeError, ValueError):
        n = LIMITE_POR_DEFECTO
    return max(1, min(n, LIMITE_MAXIMO))


def _despues_de():
    """Cursor: el id del último elemento de la página anterior."""
    valor = request.args.get('despues_de')
    if valor is None:
        return None
    try:
        return int(valor)
    except ValueError:
        return None


def _pagina(filas, limite, clave_id=0):
    """Parte el resultado en (elementos, siguiente_cursor).

    Se consulta un elemento de más que el límite: si vuelve, hay otra
    página y ese elemento no se entrega.
    """
    hay_mas = len(filas) > limite
    visibles = filas[:limite]
    siguiente = visibles[-1][clave_id] if (hay_mas and visibles) else None
    return visibles, siguiente


# ──────────────────────────── Proyectos ────────────────────────────

def _fila_proyecto(f):
    return {
        'id':               f[0],
        'nombre':           f[1],
        'cliente':          f[2],
        'contratista':      f[3],
        'ubicacion':        f[4],
        'orden_de_trabajo': f[5],
        'fecha_inicio':     f[6].isoformat() if f[6] else None,
        'estado':           f[7],
    }


_SQL_PROYECTO = """
    SELECT p.id, p.nombre_proyecto, p.cliente, p.contratista,
           p.ubicacion, p.orden_de_trabajo, p.fecha_inicio, p.estado
    FROM proyectos p
    WHERE p.empresa_id = %s
"""


@api_publica.route('/proyectos', methods=['GET'])
@requiere_api_key('proyectos:leer')
def listar_proyectos():
    """Proyectos visibles para la clave, paginados por cursor."""
    limite = _limite()
    desde = _despues_de()

    sql = _SQL_PROYECTO
    params = [g.empresa_id]

    filtro, p_filtro = filtro_proyectos('p')
    sql += filtro
    params += p_filtro

    if desde is not None:
        sql += " AND p.id > %s"
        params.append(desde)

    sql += " ORDER BY p.id LIMIT %s"
    params.append(limite + 1)

    try:
        with conexion_api() as (conn, cursor):
            cursor.execute(sql, params)
            filas = cursor.fetchall()
    except Exception:
        import traceback
        print('[API] Error listando proyectos:')
        traceback.print_exc()
        return _error(500, 'error_interno',
                      'No se pudieron consultar los proyectos.')

    visibles, siguiente = _pagina(filas, limite)

    return jsonify({
        'datos': [_fila_proyecto(f) for f in visibles],
        'paginacion': {'siguiente': siguiente},
    })


@api_publica.route('/proyectos/<int:proyecto_id>', methods=['GET'])
@requiere_api_key('proyectos:leer')
def obtener_proyecto(proyecto_id):
    """Un proyecto. Respeta la restricción de proyectos de la clave."""
    sql = _SQL_PROYECTO + " AND p.id = %s"
    params = [g.empresa_id, proyecto_id]

    filtro, p_filtro = filtro_proyectos('p')
    sql += filtro
    params += p_filtro

    try:
        with conexion_api() as (conn, cursor):
            cursor.execute(sql, params)
            fila = cursor.fetchone()
    except Exception:
        import traceback
        print('[API] Error obteniendo el proyecto:')
        traceback.print_exc()
        return _error(500, 'error_interno',
                      'No se pudo consultar el proyecto.')

    if not fila:
        # Mismo mensaje exista o no: si dijéramos "existe pero no puede
        # verlo", estaríamos confirmando datos de otra empresa.
        return _error(404, 'no_encontrado',
                      'No existe un proyecto con ese id, o la clave no '
                      'tiene acceso a él.')

    return jsonify({'datos': _fila_proyecto(fila)})


# ──────────────────────────── Formularios ────────────────────────────

@api_publica.route('/formularios', methods=['GET'])
@requiere_api_key('formularios:leer')
def listar_formularios():
    """Formularios de la empresa.

    Si la clave está limitada a ciertos proyectos, sólo se devuelven los
    formularios asignados a esos proyectos.
    """
    limite = _limite()
    desde = _despues_de()
    permitidos = getattr(g, 'proyectos_permitidos', None)

    if permitidos:
        sql = """
            SELECT DISTINCT f.id, f.nombre, f.descripcion
            FROM formularios f
            JOIN proyecto_formularios pf ON pf.formulario_id = f.id
            WHERE f.empresa_id = %s AND pf.proyecto_id = ANY(%s)
        """
        params = [g.empresa_id, list(permitidos)]
    else:
        sql = """
            SELECT f.id, f.nombre, f.descripcion
            FROM formularios f
            WHERE f.empresa_id = %s
        """
        params = [g.empresa_id]

    if desde is not None:
        sql += " AND f.id > %s"
        params.append(desde)

    sql += " ORDER BY 1 LIMIT %s"
    params.append(limite + 1)

    try:
        with conexion_api() as (conn, cursor):
            cursor.execute(sql, params)
            filas = cursor.fetchall()
    except Exception:
        import traceback
        print('[API] Error listando formularios:')
        traceback.print_exc()
        return _error(500, 'error_interno',
                      'No se pudieron consultar los formularios.')

    visibles, siguiente = _pagina(filas, limite)

    return jsonify({
        'datos': [{'id': f[0], 'nombre': f[1], 'descripcion': f[2]}
                  for f in visibles],
        'paginacion': {'siguiente': siguiente},
    })


@api_publica.route('/formularios/<int:formulario_id>', methods=['GET'])
@requiere_api_key('formularios:leer')
def obtener_formulario(formulario_id):
    """Un formulario con la definición de sus campos.

    'formularios.campos' guarda sólo referencias; las definiciones reales
    viven en 'campos_globales'.
    """
    permitidos = getattr(g, 'proyectos_permitidos', None)

    try:
        with conexion_api() as (conn, cursor):
            if permitidos:
                cursor.execute("""
                    SELECT DISTINCT f.id, f.nombre, f.descripcion, f.campos
                    FROM formularios f
                    JOIN proyecto_formularios pf ON pf.formulario_id = f.id
                    WHERE f.id = %s AND f.empresa_id = %s
                      AND pf.proyecto_id = ANY(%s)
                """, (formulario_id, g.empresa_id, list(permitidos)))
            else:
                cursor.execute("""
                    SELECT f.id, f.nombre, f.descripcion, f.campos
                    FROM formularios f
                    WHERE f.id = %s AND f.empresa_id = %s
                """, (formulario_id, g.empresa_id))

            fila = cursor.fetchone()
            if not fila:
                return _error(404, 'no_encontrado',
                              'No existe un formulario con ese id, o la '
                              'clave no tiene acceso a él.')

            referencias = fila[3] or []
            obligatorios = {}
            ids = []
            for ref in referencias:
                if isinstance(ref, dict) and 'id' in ref:
                    ids.append(ref['id'])
                    obligatorios[ref['id']] = bool(ref.get('requerido'))

            campos = []
            if ids:
                cursor.execute("""
                    SELECT id, nombre, tipo, opciones
                    FROM campos_globales
                    WHERE id = ANY(%s) AND empresa_id = %s
                """, (ids, g.empresa_id))
                definiciones = {c[0]: c for c in cursor.fetchall()}

                # Se recorre 'ids' y no el resultado para conservar el
                # orden en que el formulario declara sus campos.
                for cid in ids:
                    d = definiciones.get(cid)
                    if not d:
                        continue
                    campos.append({
                        'id':        d[0],
                        'nombre':    d[1],
                        'tipo':      d[2],
                        'opciones':  d[3] or [],
                        'requerido': obligatorios.get(cid, False),
                    })
    except Exception:
        import traceback
        print('[API] Error obteniendo el formulario:')
        traceback.print_exc()
        return _error(500, 'error_interno',
                      'No se pudo consultar el formulario.')

    return jsonify({'datos': {
        'id':          fila[0],
        'nombre':      fila[1],
        'descripcion': fila[2],
        'campos':      campos,
    }})


# ──────────────────────────── Registros ────────────────────────────

@api_publica.route('/registros', methods=['GET'])
@requiere_api_key('registros:leer')
def listar_registros():
    """Respuestas capturadas en campo.

    Filtros: proyecto_id, formulario_id, desde, hasta (fechas ISO).
    Con 'incluir=campos' se agrega al nivel raíz un diccionario que mapea
    id de campo a nombre, para no tener que pedir cada formulario aparte.
    """
    limite = _limite()
    desde_cursor = _despues_de()

    sql = """
        SELECT rf.id, rf.id_proyecto, rf.formulario_id,
               rf.created_at, rf.respuestas
        FROM respuestas_formulario rf
        JOIN proyectos p ON p.id = rf.id_proyecto
        WHERE p.empresa_id = %s
    """
    params = [g.empresa_id]

    filtro, p_filtro = filtro_proyectos('p')
    sql += filtro
    params += p_filtro

    if request.args.get('proyecto_id'):
        try:
            sql += " AND rf.id_proyecto = %s"
            params.append(int(request.args['proyecto_id']))
        except ValueError:
            return _error(400, 'parametro_invalido',
                          "'proyecto_id' debe ser un número entero.")

    if request.args.get('formulario_id'):
        try:
            sql += " AND rf.formulario_id = %s"
            params.append(int(request.args['formulario_id']))
        except ValueError:
            return _error(400, 'parametro_invalido',
                          "'formulario_id' debe ser un número entero.")

    if request.args.get('desde'):
        sql += " AND rf.created_at >= %s"
        params.append(request.args['desde'])

    if request.args.get('hasta'):
        sql += " AND rf.created_at <= %s"
        params.append(request.args['hasta'])

    if desde_cursor is not None:
        sql += " AND rf.id > %s"
        params.append(desde_cursor)

    sql += " ORDER BY rf.id LIMIT %s"
    params.append(limite + 1)

    try:
        with conexion_api() as (conn, cursor):
            cursor.execute(sql, params)
            filas = cursor.fetchall()

            visibles, siguiente = _pagina(filas, limite)

            nombres = None
            if request.args.get('incluir') == 'campos' and visibles:
                usados = set()
                for f in visibles:
                    for k in (f[4] or {}):
                        usados.add(k)
                if usados:
                    cursor.execute("""
                        SELECT id, nombre FROM campos_globales
                        WHERE id = ANY(%s) AND empresa_id = %s
                    """, ([int(u) for u in usados if str(u).isdigit()],
                          g.empresa_id))
                    nombres = {str(c[0]): c[1] for c in cursor.fetchall()}
    except Exception:
        import traceback
        print('[API] Error listando registros:')
        traceback.print_exc()
        return _error(500, 'error_interno',
                      'No se pudieron consultar los registros.')

    cuerpo = {
        'datos': [{
            'id':            f[0],
            'proyecto_id':   f[1],
            'formulario_id': f[2],
            'creado_en':     f[3].isoformat() if f[3] else None,
            'respuestas':    f[4] or {},
        } for f in visibles],
        'paginacion': {'siguiente': siguiente},
    }
    if nombres is not None:
        cuerpo['campos'] = nombres

    return jsonify(cuerpo)


@api_publica.route('/registros/<int:registro_id>', methods=['GET'])
@requiere_api_key('registros:leer')
def obtener_registro(registro_id):
    """Un registro. Respeta empresa y restricción de proyectos."""
    sql = """
        SELECT rf.id, rf.id_proyecto, rf.formulario_id,
               rf.created_at, rf.respuestas
        FROM respuestas_formulario rf
        JOIN proyectos p ON p.id = rf.id_proyecto
        WHERE p.empresa_id = %s AND rf.id = %s
    """
    params = [g.empresa_id, registro_id]

    filtro, p_filtro = filtro_proyectos('p')
    sql += filtro
    params += p_filtro

    try:
        with conexion_api() as (conn, cursor):
            cursor.execute(sql, params)
            fila = cursor.fetchone()
    except Exception:
        import traceback
        print('[API] Error obteniendo el registro:')
        traceback.print_exc()
        return _error(500, 'error_interno',
                      'No se pudo consultar el registro.')

    if not fila:
        return _error(404, 'no_encontrado',
                      'No existe un registro con ese id, o la clave no '
                      'tiene acceso a él.')

    return jsonify({'datos': {
        'id':            fila[0],
        'proyecto_id':   fila[1],
        'formulario_id': fila[2],
        'creado_en':     fila[3].isoformat() if fila[3] else None,
        'respuestas':    fila[4] or {},
    }})


# ══════════════════════════════════════════════════════════════════════
#  Administración de claves — autenticada por SESIÓN, no por clave.
#  Vive en otro blueprint a propósito: son dos modelos de autenticación
#  distintos y mezclarlos en el mismo sitio es como se cuelan los huecos.
# ══════════════════════════════════════════════════════════════════════

api_claves = Blueprint('api_claves', __name__, url_prefix='/api/claves')


def _solo_admin():
    """Devuelve una respuesta de error si quien llama no es administrador."""
    if 'user_id' not in session or session.get('user_rol') != 'admin':
        return jsonify({
            'success': False,
            'error': 'Solo los administradores pueden gestionar las claves de API.'
        }), 403
    return None


@api_claves.route('', methods=['GET'])
def listar_claves():
    """Claves de la empresa, más los datos que necesita el formulario."""
    error = _solo_admin()
    if error:
        return error

    from app import db_connection
    empresa_id = session.get('empresa_id')

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT k.id, k.nombre, k.prefijo, k.alcances, k.proyectos,
                       k.creada_en, k.expira_en, k.ultimo_uso_en, k.revocada_en,
                       u.name, u.apellido,
                       (SELECT count(*) FROM api_keys_uso x WHERE x.api_key_id = k.id)
                FROM api_keys k
                LEFT JOIN usuario u ON u.user_id = k.creada_por
                WHERE k.empresa_id = %s
                ORDER BY k.revocada_en NULLS FIRST, k.creada_en DESC
            """, (empresa_id,))
            filas = cursor.fetchall()

            cursor.execute("""
                SELECT id, nombre_proyecto FROM proyectos
                WHERE empresa_id = %s ORDER BY nombre_proyecto
            """, (empresa_id,))
            proyectos = [{'id': p[0], 'nombre': p[1]} for p in cursor.fetchall()]

    except Exception as e:
        print(f'[API] Error listando claves: {e}')
        return jsonify({'success': False, 'error': 'No se pudieron cargar las claves.'}), 500

    def fecha(d):
        return d.strftime('%Y-%m-%d') if d else None

    claves = [{
        'id':          f[0],
        'nombre':      f[1],
        'prefijo':     f[2],
        'alcances':    f[3] or [],
        'proyectos':   f[4],
        'creada_en':   fecha(f[5]),
        'expira_en':   fecha(f[6]),
        'ultimo_uso':  fecha(f[7]) or 'nunca',
        'activa':      f[8] is None,
        'creada_por':  (f'{f[9] or ""} {f[10] or ""}').strip() or 'desconocido',
        'llamadas':    f[11],
    } for f in filas]

    return jsonify({
        'success':   True,
        'claves':    claves,
        'proyectos': proyectos,
        # Sólo los de lectura: 'registros:escribir' existe pero no hay
        # endpoints todavía, y ofrecerlo sería prometer algo que no hace.
        'alcances': [
            {'id': 'proyectos:leer',   'nombre': 'Proyectos'},
            {'id': 'formularios:leer', 'nombre': 'Definición de formularios'},
            {'id': 'registros:leer',   'nombre': 'Registros de campo'},
            {'id': 'archivos:leer',    'nombre': 'Fotos y adjuntos'},
            {'id': 'analitica:leer',   'nombre': 'Indicadores'},
        ],
    })


@api_claves.route('', methods=['POST'])
def crear_clave():
    """Crea una clave y devuelve el token UNA sola vez."""
    error = _solo_admin()
    if error:
        return error

    from app import db_connection
    datos      = request.get_json(silent=True) or {}
    empresa_id = session.get('empresa_id')
    nombre     = (datos.get('nombre') or '').strip()

    if not nombre:
        return jsonify({'success': False,
                        'error': 'Ponle un nombre para saber de quién es.'}), 400

    try:
        alcances = validar_alcances(datos.get('alcances') or [])
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    # Los proyectos llegan del navegador: se aceptan sólo los que
    # realmente son de esta empresa.
    pedidos = datos.get('proyectos') or []
    proyectos = None

    token, prefijo, hash_token = generar_clave()

    try:
        with db_connection() as (conn, cursor):
            if pedidos:
                cursor.execute("""
                    SELECT id FROM proyectos
                    WHERE empresa_id = %s AND id = ANY(%s)
                """, (empresa_id, [int(p) for p in pedidos]))
                proyectos = [r[0] for r in cursor.fetchall()]
                if not proyectos:
                    return jsonify({'success': False,
                                    'error': 'Ninguno de los proyectos indicados '
                                             'pertenece a tu empresa.'}), 400

            cursor.execute("""
                INSERT INTO api_keys
                    (empresa_id, nombre, prefijo, hash, alcances,
                     proyectos, creada_por, expira_en)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (empresa_id, nombre, prefijo, hash_token, alcances,
                  proyectos, session.get('user_id'),
                  datos.get('expira_en') or None))
            nueva_id = cursor.fetchone()[0]

    except Exception as e:
        print(f'[API] Error creando la clave: {e}')
        return jsonify({'success': False,
                        'error': 'No se pudo crear la clave.'}), 500

    # El token viaja una única vez, aquí. No queda guardado en ningún lado.
    return jsonify({'success': True, 'id': nueva_id, 'token': token})


@api_claves.route('/<int:clave_id>/revocar', methods=['POST'])
def revocar_clave(clave_id):
    """Revoca sin borrar: la fila se queda para conservar la auditoría."""
    error = _solo_admin()
    if error:
        return error

    from app import db_connection
    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                UPDATE api_keys SET revocada_en = now()
                WHERE id = %s AND empresa_id = %s AND revocada_en IS NULL
            """, (clave_id, session.get('empresa_id')))
            if cursor.rowcount == 0:
                return jsonify({'success': False,
                                'error': 'Esa clave no existe o ya estaba revocada.'}), 404
    except Exception as e:
        print(f'[API] Error revocando la clave: {e}')
        return jsonify({'success': False, 'error': 'No se pudo revocar.'}), 500

    return jsonify({'success': True})


# ──────────────────────────── Archivos ────────────────────────────

# Los tipos de campo cuyo valor es un archivo en el bucket privado.
TIPOS_ARCHIVO = ('imagen', 'adjunto')

# Una hora alcanza para que un cliente descargue lo que necesite y es
# poco para que una URL filtrada sirva de algo.
VIGENCIA_URL = 3600


@api_publica.route('/registros/<int:registro_id>/archivos', methods=['GET'])
@requiere_api_key('archivos:leer')
def archivos_del_registro(registro_id):
    """URLs temporales de las fotos y adjuntos de un registro.

    Se piden por registro y no por ruta a propósito: así el acceso a un
    archivo hereda los mismos permisos del registro que lo contiene
    —empresa y restricción de proyectos— en vez de depender de adivinar
    o no una ruta.
    """
    from app import generar_url_firmada_foto

    sql = """
        SELECT rf.id, rf.formulario_id, rf.respuestas
        FROM respuestas_formulario rf
        JOIN proyectos p ON p.id = rf.id_proyecto
        WHERE p.empresa_id = %s AND rf.id = %s
    """
    params = [g.empresa_id, registro_id]

    filtro, p_filtro = filtro_proyectos('p')
    sql += filtro
    params += p_filtro

    try:
        with conexion_api() as (conn, cursor):
            cursor.execute(sql, params)
            fila = cursor.fetchone()

            if not fila:
                return _error(404, 'no_encontrado',
                              'No existe un registro con ese id, o la clave '
                              'no tiene acceso a él.')

            respuestas = fila[2] or {}
            if not respuestas:
                return jsonify({'datos': {'registro_id': registro_id,
                                          'archivos': []}})

            # Qué campos de este registro son de tipo archivo.
            ids = [int(k) for k in respuestas.keys() if str(k).isdigit()]
            if not ids:
                return jsonify({'datos': {'registro_id': registro_id,
                                          'archivos': []}})

            cursor.execute("""
                SELECT id, nombre, tipo
                FROM campos_globales
                WHERE id = ANY(%s) AND empresa_id = %s AND tipo = ANY(%s)
            """, (ids, g.empresa_id, list(TIPOS_ARCHIVO)))
            campos = {c[0]: (c[1], c[2]) for c in cursor.fetchall()}

    except Exception:
        import traceback
        print('[API] Error listando archivos del registro:')
        traceback.print_exc()
        return _error(500, 'error_interno',
                      'No se pudieron consultar los archivos.')

    archivos = []
    for campo_id, (nombre, tipo) in campos.items():
        valor = respuestas.get(str(campo_id))
        if valor is None:
            valor = respuestas.get(campo_id)
        if not valor:
            continue

        # Un campo de imagen puede traer una foto o varias.
        rutas = valor if isinstance(valor, (list, tuple)) else [valor]

        for ruta in rutas:
            if not isinstance(ruta, str) or not ruta.strip():
                continue

            firmada = generar_url_firmada_foto(ruta, VIGENCIA_URL)

            # La función devuelve la ruta tal cual cuando no puede
            # firmarla: registros viejos que guardaron sólo el nombre
            # del archivo, de antes de que las fotos vivieran en el
            # bucket privado. Se informan, pero sin URL.
            pudo_firmar = firmada != ruta

            archivos.append({
                'campo_id':   campo_id,
                'campo':      nombre,
                'tipo':       tipo,
                'ruta':       ruta,
                'url':        firmada if pudo_firmar else None,
                'disponible': pudo_firmar,
                'vigencia_segundos': VIGENCIA_URL if pudo_firmar else None,
            })

    return jsonify({'datos': {
        'registro_id': registro_id,
        'archivos':    archivos,
    }})