# ════════════════════════════════════════════════════════════════
#  Bitácora Digital — API pública para Power BI
#
#  Endpoints autenticados por token, sin sesión de Flask. Pensados
#  para que Power BI (o cualquier cliente HTTP) consuma los datos
#  sin tocar PostgreSQL directamente.
#
#  REGLA DE ORO DE ESTE MÓDULO:
#  el empresa_id SIEMPRE sale del token, nunca de la petición.
#  Un parámetro de URL jamás decide a qué organización se accede.
# ════════════════════════════════════════════════════════════════

import hashlib
import re
import secrets
from functools import wraps
from datetime import datetime
from flask import Blueprint, request, jsonify, session

bi_publica_bp = Blueprint('bi_publica', __name__)

TAM_PAGINA_DEFECTO = 1000
TAM_PAGINA_MAXIMO  = 5000

# Lo que no tiene sentido llevar a un modelo de datos: binarios y
# rutas de archivo. El resto sí viaja.
TIPOS_EXCLUIDOS = {'imagen', 'adjunto'}


# ════════════════════════════════════════════════════════════════
#  AUTENTICACIÓN POR TOKEN
# ════════════════════════════════════════════════════════════════

def _hash_token(token):
    """SHA-256 del token completo. En la base nunca vive el token
    en claro: si alguien lee la tabla, no obtiene acceso."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _token_de_la_peticion():
    """Lee el token de la cabecera Authorization: Bearer <token>.

    Power BI lo manda así desde el conector web con encabezados
    personalizados. También se acepta ?token= para poder probar
    desde el navegador, pero no es la vía recomendada: una URL
    queda en el historial y en los logs del proxy.
    """
    cabecera = request.headers.get('Authorization', '')
    if cabecera.lower().startswith('bearer '):
        return cabecera[7:].strip()
    return (request.args.get('token') or '').strip()


def requiere_token(f):
    """Valida el token y deja en request.bi los datos del dueño.

    Todo lo que decide el alcance —empresa y, si aplica, proyecto—
    queda fijado aquí y no se vuelve a leer de la petición.
    """
    @wraps(f)
    def envoltura(*args, **kwargs):
        from app import db_connection

        token = _token_de_la_peticion()
        if not token:
            return jsonify({
                'error': 'Falta el token',
                'detalle': 'Envía la cabecera: Authorization: Bearer <token>'
            }), 401

        try:
            with db_connection() as (conn, cursor):
                cursor.execute("""
                    SELECT id, empresa_id, proyecto_id, nombre, activo, expira_en
                    FROM integracion_tokens
                    WHERE token_hash = %s
                """, (_hash_token(token),))
                fila = cursor.fetchone()

                if not fila:
                    return jsonify({'error': 'Token inválido'}), 401

                tid, empresa_id, proyecto_id, nombre, activo, expira_en = fila

                if not activo:
                    return jsonify({'error': 'Token revocado'}), 403

                if expira_en and expira_en < datetime.now(expira_en.tzinfo):
                    return jsonify({'error': 'Token expirado'}), 403

                # Trazabilidad: sirve para detectar un token olvidado
                # que sigue activo, o uno que empieza a dispararse.
                cursor.execute("""
                    UPDATE integracion_tokens
                    SET ultimo_uso = NOW(), total_llamadas = total_llamadas + 1
                    WHERE id = %s
                """, (tid,))
                conn.commit()

            request.bi = {
                'token_id':    tid,
                'empresa_id':  empresa_id,
                'proyecto_id': proyecto_id,   # None = toda la empresa
                'nombre':      nombre
            }
            return f(*args, **kwargs)

        except Exception as e:
            print(f"[BI-API] Error validando el token: {e}")
            return jsonify({'error': 'Error de autenticación'}), 500

    return envoltura


def _fijar_tenant(cursor, empresa_id):
    """db_connection pone app.empresa_id desde la sesión de Flask, y
    aquí no hay sesión: quedaría en 1, que es la empresa equivocada.

    Las políticas RLS de proyectos, registros y fotos_registro leen
    esa variable, así que sin esto el cliente no vería nada de lo
    suyo. Se llama al abrir CADA conexión de este módulo.
    """
    cursor.execute("SET app.empresa_id = %s", (empresa_id,))


# ════════════════════════════════════════════════════════════════
#  APLANADO
#  Una vista SQL no sirve: cada formulario tiene campos distintos y
#  una vista tiene columnas fijas. Se aplana en Python leyendo la
#  definición real del formulario.
# ════════════════════════════════════════════════════════════════

def _columna(nombre, usadas):
    """Nombre de columna legible y único dentro de la tabla.

    Se prefiere el nombre del campo sobre un identificador técnico
    porque quien arma el informe en Power BI quiere leer 'Frente',
    no 'c_180'. El precio es que renombrar un campo en Bitácora
    cambia el nombre de la columna; por eso /formularios expone el
    mapeo campo_id → columna, para poder rastrearlo.
    """
    base = re.sub(r'\s+', ' ', str(nombre or 'campo')).strip()
    base = base.replace('"', '').replace('\n', ' ')[:60] or 'campo'

    candidato, n = base, 2
    while candidato.lower() in usadas:
        candidato = f"{base} {n}"
        n += 1
    usadas.add(candidato.lower())
    return candidato


def _definicion(cursor, empresa_id, formulario_id):
    """Campos del formulario: sueltos y dentro de grupos repetibles.

    formularios.campos guarda referencias; la definición real
    (nombre, tipo) vive en campos_globales. El recorrido es
    ordenado porque es el orden lo que indica qué campos quedaron
    dentro de cada grupo: 'grupo' abre y 'fin_grupo' cierra.
    """
    cursor.execute("""
        SELECT nombre, campos FROM formularios
        WHERE id = %s AND empresa_id = %s
    """, (formulario_id, empresa_id))
    fila = cursor.fetchone()
    if not fila:
        return None

    nombre_form, config = fila[0], (fila[1] or [])

    orden, gid, grupo_nombre = [], None, None
    grupos = {}
    for item in config:
        if isinstance(item, dict) and item.get('tipo') == 'grupo':
            gid = item.get('gid') or ''
            grupo_nombre = item.get('nombre') or 'Grupo'
            grupos[gid] = grupo_nombre
            continue
        if isinstance(item, dict) and item.get('tipo') == 'fin_grupo':
            gid, grupo_nombre = None, None
            continue
        cid = item.get('id') if isinstance(item, dict) else item
        if cid:
            orden.append((cid, gid))

    catalogo = {}
    if orden:
        cursor.execute("""
            SELECT id, nombre, tipo FROM campos_globales
            WHERE id = ANY(%s) AND empresa_id = %s
        """, ([c[0] for c in orden], empresa_id))
        catalogo = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}

    sueltos, por_grupo = [], {}
    usadas_raiz = {'registro_id', 'proyecto_id', 'proyecto',
                   'usuario_id', 'usuario', 'creado_en'}
    usadas_grupo = {}

    for cid, g in orden:
        if cid not in catalogo:
            continue
        nombre, tipo = catalogo[cid]
        if tipo in TIPOS_EXCLUIDOS:
            continue

        if g:
            usadas_grupo.setdefault(g, {'registro_id', 'bloque'})
            campo = {'campo_id': str(cid), 'tipo': tipo,
                     'columna': _columna(nombre, usadas_grupo[g])}
            por_grupo.setdefault(g, []).append(campo)
        else:
            sueltos.append({'campo_id': str(cid), 'tipo': tipo,
                            'columna': _columna(nombre, usadas_raiz)})

    return {'nombre': nombre_form, 'sueltos': sueltos,
            'grupos': grupos, 'por_grupo': por_grupo}


def _valor(bruto):
    """Normaliza un valor del JSONB a algo que Power BI entienda.

    Una selección múltiple es una lista real: se entrega como texto
    separado por '; '. Power BI puede dividirlo en columnas o en
    filas según lo necesite el modelo.
    """
    if bruto is None:
        return None
    if isinstance(bruto, list):
        return '; '.join(str(v) for v in bruto if v not in (None, ''))
    if isinstance(bruto, bool):
        return bruto
    return bruto


# ════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ════════════════════════════════════════════════════════════════

@bi_publica_bp.route('/api/bi/v1/ping', methods=['GET'])
@requiere_token
def ping():
    """Comprobar que el token sirve antes de armar nada en Power BI."""
    return jsonify({
        'ok': True,
        'token': request.bi['nombre'],
        'alcance': 'un proyecto' if request.bi['proyecto_id'] else 'toda la empresa'
    })


@bi_publica_bp.route('/api/bi/v1/proyectos', methods=['GET'])
@requiere_token
def listar_proyectos():
    """Tabla de dimensión: los proyectos a los que alcanza el token."""
    from app import db_connection

    empresa_id  = request.bi['empresa_id']
    proyecto_id = request.bi['proyecto_id']

    try:
        with db_connection() as (conn, cursor):
            _fijar_tenant(cursor, empresa_id)

            sql = """
                SELECT id, nombre_proyecto, cliente, ubicacion, fecha_inicio
                FROM proyectos
                WHERE empresa_id = %s
            """
            par = [empresa_id]
            if proyecto_id:
                sql += " AND id = %s"
                par.append(proyecto_id)
            sql += " ORDER BY nombre_proyecto"

            cursor.execute(sql, par)
            proyectos = [{
                'proyecto_id':  r[0],
                'proyecto':     r[1],
                'cliente':      r[2],
                'ubicacion':    r[3],
                'fecha_inicio': r[4].isoformat() if r[4] else None
            } for r in cursor.fetchall()]

        return jsonify({'proyectos': proyectos, 'total': len(proyectos)})

    except Exception as e:
        print(f"[BI-API] Error listando proyectos: {e}")
        return jsonify({'error': 'Error consultando proyectos'}), 500


@bi_publica_bp.route('/api/bi/v1/formularios', methods=['GET'])
@requiere_token
def listar_formularios():
    """Descubrimiento del esquema: qué tablas puede traer el cliente
    y qué columnas trae cada una.

    Es lo primero que se consulta al armar el modelo en Power BI:
    cada formulario será una tabla, y cada grupo repetible una
    tabla relacionada por registro_id.
    """
    from app import db_connection

    empresa_id  = request.bi['empresa_id']
    proyecto_id = request.bi['proyecto_id']

    try:
        with db_connection() as (conn, cursor):
            _fijar_tenant(cursor, empresa_id)

            # Solo formularios con datos dentro del alcance del token:
            # ofrecer una tabla vacía solo genera confusión en el modelo.
            sql = """
                SELECT DISTINCT f.id
                FROM formularios f
                JOIN respuestas_formulario rf ON rf.formulario_id = f.id
                JOIN proyectos p              ON p.id = rf.id_proyecto
                WHERE f.empresa_id = %s AND p.empresa_id = %s
            """
            par = [empresa_id, empresa_id]
            if proyecto_id:
                sql += " AND rf.id_proyecto = %s"
                par.append(proyecto_id)

            cursor.execute(sql, par)
            ids = [r[0] for r in cursor.fetchall()]

            formularios = []
            for fid in ids:
                d = _definicion(cursor, empresa_id, fid)
                if not d:
                    continue

                formularios.append({
                    'formulario_id': fid,
                    'nombre':        d['nombre'],
                    'url':           f"/api/bi/v1/registros?formulario_id={fid}",
                    'columnas_fijas': ['registro_id', 'proyecto_id', 'proyecto',
                                       'usuario_id', 'usuario', 'creado_en'],
                    'columnas': [{'campo_id': c['campo_id'],
                                  'columna':  c['columna'],
                                  'tipo':     c['tipo']} for c in d['sueltos']],
                    'grupos': [{
                        'gid':     g,
                        'nombre':  d['grupos'].get(g, 'Grupo'),
                        'url':     f"/api/bi/v1/registros?formulario_id={fid}&grupo={g}",
                        'columnas': [{'campo_id': c['campo_id'],
                                      'columna':  c['columna'],
                                      'tipo':     c['tipo']} for c in campos]
                    } for g, campos in (d['por_grupo'] or {}).items()]
                })

        return jsonify({'formularios': formularios, 'total': len(formularios)})

    except Exception as e:
        print(f"[BI-API] Error listando formularios: {e}")
        return jsonify({'error': 'Error consultando formularios'}), 500


@bi_publica_bp.route('/api/bi/v1/registros', methods=['GET'])
@requiere_token
def listar_registros():
    """Las filas, ya aplanadas y paginadas.

    Sin 'grupo'  → una fila por respuesta, con los campos sueltos.
    Con 'grupo'  → una fila por bloque repetible, ligada por
                   registro_id a la tabla principal.
    """
    from app import db_connection

    empresa_id    = request.bi['empresa_id']
    alcance       = request.bi['proyecto_id']
    formulario_id = request.args.get('formulario_id', type=int)
    gid           = (request.args.get('grupo') or '').strip()
    pagina        = max(1, request.args.get('pagina', 1, type=int))
    desde         = (request.args.get('desde') or '').strip()
    hasta         = (request.args.get('hasta') or '').strip()

    tam = request.args.get('tam', TAM_PAGINA_DEFECTO, type=int)
    tam = max(1, min(tam, TAM_PAGINA_MAXIMO))

    if not formulario_id:
        return jsonify({'error': 'Falta formulario_id',
                        'detalle': 'Consulta /api/bi/v1/formularios para ver cuáles hay'}), 400

    # El proyecto pedido nunca amplía el alcance del token: si el
    # token está acotado, manda el token.
    proyecto_pedido = request.args.get('proyecto_id', type=int)
    proyecto_id = alcance or proyecto_pedido

    try:
        with db_connection() as (conn, cursor):
            _fijar_tenant(cursor, empresa_id)

            d = _definicion(cursor, empresa_id, formulario_id)
            if not d:
                return jsonify({'error': 'Formulario no encontrado'}), 404

            if gid and gid not in (d['por_grupo'] or {}):
                return jsonify({'error': 'Ese grupo no existe en el formulario'}), 404

            # ── Filtros comunes ──
            where = ["rf.formulario_id = %s", "p.empresa_id = %s"]
            par   = [formulario_id, empresa_id]
            if proyecto_id:
                where.append("rf.id_proyecto = %s"); par.append(proyecto_id)
            if desde:
                where.append("rf.created_at::date >= %s"); par.append(desde)
            if hasta:
                where.append("rf.created_at::date <= %s"); par.append(hasta)
            filtro = " AND ".join(where)

            cursor.execute(f"""
                SELECT COUNT(*) FROM respuestas_formulario rf
                JOIN proyectos p ON p.id = rf.id_proyecto
                WHERE {filtro}
            """, par)
            total = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT rf.id, rf.id_proyecto, p.nombre_proyecto,
                       rf.user_id,
                       COALESCE(u.name || ' ' || COALESCE(u.apellido, ''), '') AS autor,
                       rf.created_at, rf.respuestas
                FROM respuestas_formulario rf
                JOIN proyectos p     ON p.id = rf.id_proyecto
                LEFT JOIN usuario u  ON u.user_id = rf.user_id
                WHERE {filtro}
                ORDER BY rf.id
                LIMIT %s OFFSET %s
            """, par + [tam, (pagina - 1) * tam])

            filas = []
            for rid, pid, pnombre, uid, autor, creado, resp in cursor.fetchall():
                resp = resp or {}
                base = {
                    'registro_id': rid,
                    'proyecto_id': pid,
                    'proyecto':    pnombre,
                    'usuario_id':  uid,
                    'usuario':     (autor or '').strip(),
                    'creado_en':   creado.isoformat() if creado else None
                }

                if not gid:
                    for campo in d['sueltos']:
                        base[campo['columna']] = _valor(resp.get(campo['campo_id']))
                    filas.append(base)
                    continue

                # Un bloque repetible por fila, ligado por registro_id.
                bloques = (resp.get('__repeticiones') or {}).get(gid) or []
                for i, bloque in enumerate(bloques, start=1):
                    if not isinstance(bloque, dict):
                        continue
                    fila = dict(base)
                    fila['bloque'] = i
                    for campo in d['por_grupo'][gid]:
                        fila[campo['columna']] = _valor(bloque.get(campo['campo_id']))
                    filas.append(fila)

        paginas = (total + tam - 1) // tam if total else 0
        return jsonify({
            'formulario_id': formulario_id,
            'formulario':    d['nombre'],
            'grupo':         gid or None,
            'pagina':        pagina,
            'tam':           tam,
            'total':         total,
            'paginas':       paginas,
            'hay_mas':       pagina < paginas,
            'filas':         filas
        })

    except Exception as e:
        print(f"[BI-API] Error consultando registros: {e}")
        return jsonify({'error': 'Error consultando registros'}), 500


# ════════════════════════════════════════════════════════════════
#  GESTIÓN DE TOKENS
#  Esta parte sí usa sesión de Flask: la opera el administrador
#  desde Configuración → Integraciones, no Power BI.
# ════════════════════════════════════════════════════════════════

PREFIJO_TOKEN = 'bitk_'
LARGO_PREFIJO = 12       # lo que se guarda para reconocerlo en la lista


def _admin_de_empresa():
    """(empresa_id, user_id) si hay administrador en sesión."""
    if session.get('user_rol') != 'admin':
        return None, None
    return session.get('empresa_id'), session.get('user_id')


def _generar_token():
    """Devuelve (token_completo, hash, prefijo).

    32 bytes de secrets.token_urlsafe: el mismo orden de magnitud
    que un token de GitHub. El token completo se devuelve una sola
    vez y no vuelve a existir en ningún lado.
    """
    token   = PREFIJO_TOKEN + secrets.token_urlsafe(32)
    return token, _hash_token(token), token[:LARGO_PREFIJO]


@bi_publica_bp.route('/api/integraciones/tokens', methods=['GET'])
def listar_tokens():
    """Tokens de la empresa. Nunca el token: solo su prefijo.

    Devuelve también los proyectos, que es lo que necesita el modal
    de creación para el desplegable de alcance.
    """
    from app import db_connection

    empresa_id, user_id = _admin_de_empresa()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT t.id, t.nombre, t.prefijo, t.activo,
                       t.proyecto_id, p.nombre_proyecto,
                       t.ultimo_uso, t.total_llamadas,
                       t.expira_en, t.created_at,
                       COALESCE(u.name, '') AS creador
                FROM integracion_tokens t
                LEFT JOIN proyectos p ON p.id = t.proyecto_id
                LEFT JOIN usuario   u ON u.user_id = t.creado_por
                WHERE t.empresa_id = %s
                ORDER BY t.activo DESC, t.created_at DESC
            """, (empresa_id,))

            tokens = [{
                'id':             r[0],
                'nombre':         r[1],
                'prefijo':        r[2],
                'activo':         r[3],
                'proyecto_id':    r[4],
                'alcance':        r[5] or 'Toda la empresa',
                'ultimo_uso':     r[6].strftime('%d/%m/%Y %H:%M') if r[6] else 'Nunca',
                'total_llamadas': r[7] or 0,
                'expira_en':      r[8].strftime('%d/%m/%Y') if r[8] else None,
                'creado':         r[9].strftime('%d/%m/%Y') if r[9] else '',
                'creador':        r[10]
            } for r in cursor.fetchall()]

            cursor.execute("""
                SELECT id, nombre_proyecto FROM proyectos
                WHERE empresa_id = %s
                ORDER BY nombre_proyecto
            """, (empresa_id,))
            proyectos = [{'id': r[0], 'nombre': r[1]} for r in cursor.fetchall()]

        return jsonify({'success': True, 'tokens': tokens, 'proyectos': proyectos})

    except Exception as e:
        print(f"[BI-TOKENS] Error listando: {e}")
        return jsonify({'error': str(e)}), 500


@bi_publica_bp.route('/api/integraciones/tokens', methods=['POST'])
def crear_token():
    """Crea un token y lo devuelve EN CLARO, una sola vez.

    En la base queda únicamente el hash. No existe forma de volver
    a leerlo, y es a propósito: si el servidor pudiera recuperarlo,
    quien entrara a la base también podría.
    """
    from app import db_connection

    empresa_id, user_id = _admin_de_empresa()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 403

    data   = request.get_json() or {}
    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({'success': False,
                        'error': 'Ponle un nombre para saber de quién es'}), 400

    try:
        with db_connection() as (conn, cursor):
            # El proyecto debe ser de esta empresa. Si no lo es, el
            # token queda sin acotar en vez de acotarse al proyecto
            # de otro: fallar hacia la empresa propia, nunca hacia afuera.
            proyecto_id = data.get('proyecto_id') or None
            if proyecto_id:
                cursor.execute("""
                    SELECT 1 FROM proyectos WHERE id = %s AND empresa_id = %s
                """, (proyecto_id, empresa_id))
                if not cursor.fetchone():
                    proyecto_id = None

            expira_en = (data.get('expira_en') or '').strip() or None

            token, hash_token, prefijo = _generar_token()

            cursor.execute("""
                INSERT INTO integracion_tokens
                    (empresa_id, nombre, token_hash, prefijo,
                     proyecto_id, expira_en, activo, creado_por)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
                RETURNING id
            """, (empresa_id, nombre, hash_token, prefijo,
                  proyecto_id, expira_en, user_id))
            nuevo_id = cursor.fetchone()[0]
            conn.commit()

        return jsonify({
            'success': True,
            'id':      nuevo_id,
            'token':   token,       # ← única vez que viaja en claro
            'aviso':   'Cópialo ahora. No se puede volver a consultar.'
        })

    except Exception as e:
        print(f"[BI-TOKENS] Error creando: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bi_publica_bp.route('/api/integraciones/tokens/<int:token_id>/revocar', methods=['POST'])
def revocar_token(token_id):
    """Desactiva un token. No hay reactivación a propósito.

    Un token se revoca porque se filtró o porque el cliente dejó de
    necesitarlo. En el primer caso, reactivarlo sería devolverle el
    acceso a quien lo robó. Si hace falta de nuevo, se emite uno
    nuevo — es gratis y deja rastro de cuándo se hizo.
    """
    from app import db_connection

    empresa_id, user_id = _admin_de_empresa()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                UPDATE integracion_tokens SET activo = FALSE
                WHERE id = %s AND empresa_id = %s
                RETURNING nombre
            """, (token_id, empresa_id))
            fila = cursor.fetchone()
            if not fila:
                return jsonify({'error': 'Token no encontrado'}), 404
            conn.commit()

        return jsonify({'success': True, 'nombre': fila[0]})

    except Exception as e:
        print(f"[BI-TOKENS] Error revocando {token_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bi_publica_bp.route('/api/integraciones/tokens/<int:token_id>', methods=['DELETE'])
def eliminar_token(token_id):
    """Borra el registro por completo.

    Revocar deja el rastro de que existió y cuánto se usó; eliminar
    lo quita de la lista. Para un token que se filtró, revocar es
    mejor: conserva la evidencia.
    """
    from app import db_connection

    empresa_id, user_id = _admin_de_empresa()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                DELETE FROM integracion_tokens
                WHERE id = %s AND empresa_id = %s
            """, (token_id, empresa_id))
            conn.commit()

        return jsonify({'success': True})

    except Exception as e:
        print(f"[BI-TOKENS] Error eliminando {token_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500