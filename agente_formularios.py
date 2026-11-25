"""
agente_formularios.py — Asistente que arma formularios conversando.

El usuario describe lo que necesita y el agente propone un formulario:
qué campos reusar de los que la empresa ya tiene y cuáles crear nuevos.

El agente NUNCA escribe en la base por su cuenta. Entrega una propuesta;
el administrador la confirma y ahí sí se crea todo, en una operación.
"""

import json

from flask import Blueprint, jsonify, request, session

# Los tipos que Bitácora sabe representar. El modelo recibe esta lista en
# el esquema de la herramienta, y el servidor la vuelve a validar: una
# salida del modelo no es una entrada confiable.
TIPOS_PERMITIDOS = {
    'texto_corto', 'texto_largo', 'numero', 'imagen', 'fecha', 'hora',
    'seleccion', 'seleccion_unica', 'adjunto', 'booleano',
    'moneda', 'porcentaje',
}

TIPOS_CON_OPCIONES = {'seleccion', 'seleccion_unica'}

MODELO = 'gpt-4o'
MAX_VUELTAS = 4          # tope de idas y vueltas con herramientas
MAX_CAMPOS = 40
MAX_HISTORIAL = 20       # mensajes que se le pasan al modelo


INSTRUCCIONES = """Eres el asistente de formularios de Bitácora Digital, una \
aplicación donde se registra lo que pasa en obras y plantas de producción.

Haces dos cosas: armar formularios, y crear campos globales sueltos cuando el \
administrador quiere dejarlos listos antes de usarlos en algún formulario. No \
consultas registros, no generas informes y no respondes preguntas sobre los \
datos de la empresa. Si te piden algo de eso, dilo en una frase y ofrece lo \
que sí puedes hacer.

CÓMO TRABAJAS

1. Antes de proponer nada, usa `buscar_campos` para ver qué campos ya existen \
en la empresa. Hazlo siempre, con varios términos a la vez.

2. Reutiliza. Si ya existe un campo equivalente, úsalo aunque el nombre no \
sea idéntico: «Observaciones» sirve para lo que el usuario llamó «Notas» o \
«Comentarios». Crear duplicados ensucia la configuración de la empresa y \
rompe sus tableros, porque el mismo dato queda repartido en campos distintos.

3. Crea un campo nuevo sólo cuando de verdad no exista algo equivalente.

4. Cuando tengas la estructura, llama a `proponer_formulario`. No describas \
el formulario en texto: entrégalo por la herramienta.

5. Si te piden campos sueltos, sin formulario, usa `proponer_campos`. Busca \
primero igual que siempre: si alguno ya existe, dilo en vez de duplicarlo.

CRITERIO

- Pocos campos y bien pensados. Un formulario de campo se llena de pie, con \
guantes y con el celular. Diez campos es mucho.
- Elige el tipo por cómo se captura el dato, no por cómo se ve: una cantidad \
es `numero`, no `texto_corto`.
- Para selecciones, propón opciones plausibles del sector, y avísale al \
usuario que las revise: tú no conoces las que usa su empresa.
- Si la petición es ambigua en algo que cambia la estructura, pregunta una \
sola cosa, la más importante. No interrogues.

Responde en español, breve y sin rodeos."""


HERRAMIENTAS = [
    {
        'type': 'function',
        'function': {
            'name': 'buscar_campos',
            'description': ('Busca campos que ya existen en la empresa. '
                            'Úsala siempre antes de proponer un formulario.'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'terminos': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': ('Palabras a buscar, por ejemplo '
                                        '["observaciones", "cantidad", "fecha"]. '
                                        'Manda varias de una vez.'),
                    }
                },
                'required': ['terminos'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'proponer_formulario',
            'description': 'Entrega la estructura final del formulario.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'nombre':      {'type': 'string'},
                    'descripcion': {'type': 'string'},
                    'campos': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'accion': {'type': 'string',
                                           'enum': ['reusar', 'crear']},
                                'id':     {'type': 'integer',
                                           'description': 'Sólo si accion es reusar'},
                                'nombre': {'type': 'string',
                                           'description': 'Sólo si accion es crear'},
                                'tipo':   {'type': 'string',
                                           'enum': sorted(TIPOS_PERMITIDOS),
                                           'description': 'Sólo si accion es crear'},
                                'opciones': {'type': 'array',
                                             'items': {'type': 'string'}},
                                'requerido': {'type': 'boolean'},
                            },
                            'required': ['accion'],
                        },
                    },
                },
                'required': ['nombre', 'campos'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'proponer_campos',
            'description': ('Entrega campos globales sueltos, sin formulario. '
                            'Úsala sólo si el usuario pidió campos, no un '
                            'formulario.'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'campos': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'nombre': {'type': 'string'},
                                'tipo':   {'type': 'string',
                                           'enum': sorted(TIPOS_PERMITIDOS)},
                                'opciones': {'type': 'array',
                                             'items': {'type': 'string'}},
                            },
                            'required': ['nombre', 'tipo'],
                        },
                    },
                },
                'required': ['campos'],
            },
        },
    },
]


# ──────────────────────────── Herramientas ────────────────────────────

def buscar_campos(cursor, empresa_id, terminos):
    """Campos de la empresa que coincidan con alguno de los términos.

    El filtro por empresa va aquí, del lado del servidor. Es la frontera
    real: si alguien intenta convencer al modelo de mirar otra empresa,
    la consulta simplemente no se lo permite.
    """
    terminos = [t.strip() for t in (terminos or []) if t and t.strip()][:8]
    if not terminos:
        return []

    patrones = [f'%{t}%' for t in terminos]
    cursor.execute("""
        SELECT id, nombre, tipo, opciones
        FROM campos_globales
        WHERE empresa_id = %s
          AND objeto = 'formulario'
          AND nombre ILIKE ANY(%s)
        ORDER BY nombre
        LIMIT 60
    """, (empresa_id, patrones))

    return [{
        'id':       f[0],
        'nombre':   f[1],
        'tipo':     f[2],
        # Sólo las primeras: al modelo le basta para saber de qué va el
        # campo, y mandarle doscientas opciones es gastar contexto.
        'opciones': (f[3] or [])[:8] if isinstance(f[3], list) else [],
    } for f in cursor.fetchall()]


# ──────────────────────────── Validación ────────────────────────────

def validar_propuesta(cursor, empresa_id, cruda):
    """Normaliza y verifica la propuesta del modelo.

    Devuelve (propuesta, avisos). Levanta ValueError si es inservible.
    """
    avisos = []
    nombre = (cruda.get('nombre') or '').strip()
    if not nombre:
        raise ValueError('La propuesta no trae nombre de formulario.')

    campos_crudos = cruda.get('campos') or []
    if not campos_crudos:
        raise ValueError('La propuesta no trae campos.')
    if len(campos_crudos) > MAX_CAMPOS:
        raise ValueError(f'La propuesta trae más de {MAX_CAMPOS} campos.')

    # Qué campos existen hoy, para validar los reusados y para detectar
    # un "nuevo" que en realidad ya existe.
    cursor.execute("""
        SELECT id, nombre, tipo FROM campos_globales
        WHERE empresa_id = %s AND objeto = 'formulario'
    """, (empresa_id,))
    existentes = cursor.fetchall()
    por_id     = {c[0]: (c[1], c[2]) for c in existentes}
    por_nombre = {(c[1] or '').strip().lower(): (c[0], c[2]) for c in existentes}

    campos = []
    vistos_id = set()
    vistos_nombre = set()

    for bruto in campos_crudos:
        accion    = (bruto.get('accion') or '').strip()
        requerido = bool(bruto.get('requerido'))

        if accion == 'reusar':
            cid = bruto.get('id')
            if cid not in por_id:
                avisos.append(f'Se descartó un campo reusado que no existe (id {cid}).')
                continue
            if cid in vistos_id:
                continue
            vistos_id.add(cid)
            campos.append({'accion': 'reusar', 'id': cid,
                           'nombre': por_id[cid][0], 'tipo': por_id[cid][1],
                           'requerido': requerido})
            continue

        if accion != 'crear':
            avisos.append('Se descartó un campo con acción desconocida.')
            continue

        nom = (bruto.get('nombre') or '').strip()
        tipo = (bruto.get('tipo') or '').strip()

        if not nom:
            avisos.append('Se descartó un campo nuevo sin nombre.')
            continue
        if tipo not in TIPOS_PERMITIDOS:
            avisos.append(f'Se descartó «{nom}»: el tipo «{tipo}» no existe en Bitácora.')
            continue

        clave = nom.lower()

        # Red de seguridad contra la duplicación: si el modelo propuso
        # crear algo que ya existe con el mismo nombre, se reusa. Es el
        # error que más daño hace a la configuración de una empresa.
        if clave in por_nombre:
            cid, tipo_existente = por_nombre[clave]
            if cid not in vistos_id:
                vistos_id.add(cid)
                campos.append({'accion': 'reusar', 'id': cid,
                               'nombre': nom, 'tipo': tipo_existente,
                               'requerido': requerido})
                avisos.append(f'«{nom}» ya existía: se reutilizó en vez de crearlo.')
            continue

        if clave in vistos_nombre:
            continue
        vistos_nombre.add(clave)

        opciones = [str(o).strip() for o in (bruto.get('opciones') or [])
                    if str(o).strip()][:50]

        if tipo in TIPOS_CON_OPCIONES and not opciones:
            avisos.append(f'«{nom}» es de selección pero no trae opciones; '
                          f'habrá que agregarlas.')
        if tipo in TIPOS_CON_OPCIONES and opciones:
            avisos.append(f'Las opciones de «{nom}» son una sugerencia: revísalas.')

        campos.append({'accion': 'crear', 'nombre': nom, 'tipo': tipo,
                       'opciones': opciones, 'requerido': requerido})

    if not campos:
        raise ValueError('Ningún campo de la propuesta resultó válido.')

    propuesta = {
        'tipo':        'formulario',
        'nombre':      nombre[:120],
        'descripcion': (cruda.get('descripcion') or '').strip()[:300],
        'campos':      campos,
        'reusados':    sum(1 for c in campos if c['accion'] == 'reusar'),
        'nuevos':      sum(1 for c in campos if c['accion'] == 'crear'),
    }
    return propuesta, avisos


# ──────────────────────────── Conversación ────────────────────────────

def responder(cursor, empresa_id, historial):
    """Una vuelta del asistente.

    'historial' son los mensajes previos en formato OpenAI. Devuelve
    (texto, propuesta, avisos); la propuesta puede ser None.
    """
    from app import openai_client

    mensajes = [{'role': 'system', 'content': INSTRUCCIONES}]
    mensajes += historial[-MAX_HISTORIAL:]

    for _ in range(MAX_VUELTAS):
        respuesta = openai_client.chat.completions.create(
            model=MODELO,
            messages=mensajes,
            tools=HERRAMIENTAS,
            temperature=0.2,
        )
        mensaje = respuesta.choices[0].message

        if not mensaje.tool_calls:
            return (mensaje.content or '').strip(), None, []

        # El historial que vuelve al modelo debe incluir su propia
        # llamada; si no, en la siguiente vuelta no sabe qué pidió.
        mensajes.append({
            'role': 'assistant',
            'content': mensaje.content,
            'tool_calls': [{
                'id': t.id,
                'type': 'function',
                'function': {'name': t.function.name,
                             'arguments': t.function.arguments},
            } for t in mensaje.tool_calls],
        })

        for llamada in mensaje.tool_calls:
            nombre = llamada.function.name
            try:
                args = json.loads(llamada.function.arguments or '{}')
            except json.JSONDecodeError:
                args = {}

            if nombre == 'buscar_campos':
                resultado = buscar_campos(cursor, empresa_id,
                                          args.get('terminos'))
                mensajes.append({
                    'role': 'tool',
                    'tool_call_id': llamada.id,
                    'content': json.dumps({'campos': resultado},
                                          ensure_ascii=False),
                })

            elif nombre == 'proponer_formulario':
                try:
                    propuesta, avisos = validar_propuesta(cursor, empresa_id, args)
                except ValueError as e:
                    # Se le devuelve el error al modelo para que corrija
                    # en la siguiente vuelta, en vez de fallar de plano.
                    mensajes.append({
                        'role': 'tool',
                        'tool_call_id': llamada.id,
                        'content': json.dumps({'error': str(e)},
                                              ensure_ascii=False),
                    })
                    continue

                texto = (f'Te propongo «{propuesta["nombre"]}» con '
                         f'{len(propuesta["campos"])} campos: '
                         f'{propuesta["reusados"]} que ya tenías y '
                         f'{propuesta["nuevos"]} nuevos.')
                return texto, propuesta, avisos

            elif nombre == 'proponer_campos':
                # Se reusa la misma validación con un nombre de relleno:
                # las reglas de tipos y de duplicados son idénticas.
                try:
                    propuesta, avisos = validar_propuesta(
                        cursor, empresa_id,
                        {'nombre': 'campos',
                         'campos': [dict(c, accion='crear')
                                    for c in (args.get('campos') or [])]})
                except ValueError as e:
                    mensajes.append({
                        'role': 'tool',
                        'tool_call_id': llamada.id,
                        'content': json.dumps({'error': str(e)},
                                              ensure_ascii=False),
                    })
                    continue

                propuesta['tipo'] = 'campos'
                propuesta.pop('nombre', None)

                if propuesta['nuevos'] == 0:
                    return ('Todos esos campos ya existen en tu empresa, '
                            'así que no hace falta crear ninguno.'), None, avisos

                texto = (f'Te propongo crear {propuesta["nuevos"]} '
                         f'campo{"s" if propuesta["nuevos"] != 1 else ""}.')
                return texto, propuesta, avisos

            else:
                mensajes.append({
                    'role': 'tool',
                    'tool_call_id': llamada.id,
                    'content': json.dumps({'error': 'herramienta desconocida'}),
                })

    return ('No logré armar el formulario con lo que me diste. '
            '¿Puedes describirlo de otra forma?'), None, []


# ──────────────────────── Creación confirmada ────────────────────────

def crear_desde_propuesta(cursor, empresa_id, user_id, propuesta):
    """Crea los campos nuevos y el formulario. Lo dispara el usuario.

    Es el único punto que escribe. Va todo en la misma transacción: o
    queda el formulario completo, o no queda nada a medias.
    """
    # Campos sueltos: se crean y se acaba ahí, sin formulario.
    if propuesta.get('tipo') == 'campos':
        creados = 0
        for campo in propuesta.get('campos', []):
            if campo.get('accion') != 'crear':
                continue
            cursor.execute("""
                INSERT INTO campos_globales
                    (empresa_id, nombre, tipo, objeto, opciones, configuracion)
                VALUES (%s, %s, %s, 'formulario', %s::jsonb, '{}'::jsonb)
                RETURNING id
            """, (empresa_id, campo['nombre'], campo['tipo'],
                  json.dumps(campo.get('opciones') or [])))
            cursor.fetchone()
            creados += 1

        if not creados:
            raise ValueError('No quedó ningún campo nuevo por crear.')
        return {'tipo': 'campos', 'creados': creados}

    referencias = []

    for campo in propuesta.get('campos', []):
        if campo['accion'] == 'reusar':
            # Se revalida contra la base: la propuesta estuvo guardada un
            # rato y alguien pudo borrar el campo mientras tanto.
            cursor.execute("""
                SELECT 1 FROM campos_globales
                WHERE id = %s AND empresa_id = %s
            """, (campo['id'], empresa_id))
            if not cursor.fetchone():
                continue
            referencias.append({'id': campo['id'],
                                'requerido': bool(campo.get('requerido'))})
            continue

        cursor.execute("""
            INSERT INTO campos_globales
                (empresa_id, nombre, tipo, objeto, opciones, configuracion)
            VALUES (%s, %s, %s, 'formulario', %s::jsonb, '{}'::jsonb)
            RETURNING id
        """, (empresa_id, campo['nombre'], campo['tipo'],
              json.dumps(campo.get('opciones') or [])))
        referencias.append({'id': cursor.fetchone()[0],
                            'requerido': bool(campo.get('requerido'))})

    if not referencias:
        raise ValueError('No quedó ningún campo válido para crear el formulario.')

    cursor.execute("""
        INSERT INTO formularios (empresa_id, nombre, descripcion, campos)
        VALUES (%s, %s, %s, %s::jsonb)
        RETURNING id
    """, (empresa_id, propuesta['nombre'],
          propuesta.get('descripcion') or '', json.dumps(referencias)))

    return {'tipo': 'formulario', 'formulario_id': cursor.fetchone()[0]}

# ══════════════════════════════════════════════════════════════════════
#  Endpoints — autenticados por sesión, sólo administradores.
# ══════════════════════════════════════════════════════════════════════

agente_bp = Blueprint('agente_bp', __name__, url_prefix='/api/agente')

# Tope por conversación: evita que alguien lo use como chat general y
# que el historial crezca hasta encarecer cada llamada.
MAX_MENSAJES_CONVERSACION = 40


def _solo_admin():
    if 'user_id' not in session or session.get('user_rol') != 'admin':
        return jsonify({'success': False,
                        'error': 'Solo los administradores pueden usar el '
                                 'asistente de formularios.'}), 403
    return None


def _conversacion_activa(cursor, empresa_id, user_id, crear=True):
    """La conversación abierta del usuario, o una nueva."""
    cursor.execute("""
        SELECT id FROM agente_conversaciones
        WHERE empresa_id = %s AND user_id = %s AND cerrado_en IS NULL
        ORDER BY creado_en DESC
        LIMIT 1
    """, (empresa_id, user_id))
    fila = cursor.fetchone()
    if fila:
        return fila[0]
    if not crear:
        return None

    cursor.execute("""
        INSERT INTO agente_conversaciones (empresa_id, user_id)
        VALUES (%s, %s) RETURNING id
    """, (empresa_id, user_id))
    return cursor.fetchone()[0]


def _mensajes(cursor, conversacion_id):
    cursor.execute("""
        SELECT id, rol, contenido, propuesta, creado_en
        FROM agente_mensajes
        WHERE conversacion_id = %s
        ORDER BY creado_en, id
    """, (conversacion_id,))
    return cursor.fetchall()


@agente_bp.route('/conversacion', methods=['GET'])
def obtener_conversacion():
    """Historial de la conversación abierta. Si no hay, devuelve vacío."""
    error = _solo_admin()
    if error:
        return error

    from app import db_connection
    empresa_id = session.get('empresa_id')
    user_id    = session.get('user_id')

    try:
        with db_connection() as (conn, cursor):
            conv_id = _conversacion_activa(cursor, empresa_id, user_id,
                                           crear=False)
            filas = _mensajes(cursor, conv_id) if conv_id else []
    except Exception as e:
        print(f'[AGENTE] Error cargando la conversación: {e}')
        return jsonify({'success': False,
                        'error': 'No se pudo cargar la conversación.'}), 500

    return jsonify({
        'success': True,
        'conversacion_id': conv_id,
        'mensajes': [{
            'id':        f[0],
            'rol':       f[1],
            'contenido': f[2],
            'propuesta': f[3],
            'creado_en': f[4].strftime('%H:%M') if f[4] else None,
        } for f in filas],
    })


@agente_bp.route('/nueva', methods=['POST'])
def nueva_conversacion():
    """Cierra la conversación actual para empezar de cero."""
    error = _solo_admin()
    if error:
        return error

    from app import db_connection
    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                UPDATE agente_conversaciones SET cerrado_en = now()
                WHERE empresa_id = %s AND user_id = %s AND cerrado_en IS NULL
            """, (session.get('empresa_id'), session.get('user_id')))
    except Exception as e:
        print(f'[AGENTE] Error cerrando la conversación: {e}')
        return jsonify({'success': False, 'error': 'No se pudo reiniciar.'}), 500

    return jsonify({'success': True})


@agente_bp.route('/mensaje', methods=['POST'])
def enviar_mensaje():
    """Guarda el mensaje del usuario y devuelve la respuesta del asistente."""
    error = _solo_admin()
    if error:
        return error

    from app import db_connection
    texto = ((request.get_json(silent=True) or {}).get('texto') or '').strip()
    if not texto:
        return jsonify({'success': False, 'error': 'Escribe algo.'}), 400
    if len(texto) > 2000:
        return jsonify({'success': False,
                        'error': 'El mensaje es demasiado largo.'}), 400

    empresa_id = session.get('empresa_id')
    user_id    = session.get('user_id')

    try:
        with db_connection() as (conn, cursor):
            conv_id = _conversacion_activa(cursor, empresa_id, user_id)
            previos = _mensajes(cursor, conv_id)

            if len(previos) >= MAX_MENSAJES_CONVERSACION:
                return jsonify({
                    'success': False,
                    'error': 'Esta conversación ya está muy larga. '
                             'Empieza una nueva para seguir.'}), 400

            cursor.execute("""
                INSERT INTO agente_mensajes (conversacion_id, rol, contenido)
                VALUES (%s, 'usuario', %s)
            """, (conv_id, texto))

            # El historial que ve el modelo lleva sólo el texto. Las
            # llamadas a herramientas de vueltas anteriores no se guardan:
            # emparejar sus identificadores entre peticiones es frágil, y
            # volver a buscar campos cuesta poco y da datos frescos.
            historial = [{'role': 'user' if f[1] == 'usuario' else 'assistant',
                          'content': f[2] or ''} for f in previos]
            historial.append({'role': 'user', 'content': texto})

            respuesta, propuesta, avisos = responder(cursor, empresa_id,
                                                     historial)

            cursor.execute("""
                INSERT INTO agente_mensajes
                    (conversacion_id, rol, contenido, propuesta)
                VALUES (%s, 'asistente', %s, %s::jsonb)
                RETURNING id
            """, (conv_id, respuesta,
                  json.dumps(propuesta) if propuesta else None))
            mensaje_id = cursor.fetchone()[0]

            # El título se fija con la primera frase: es lo que identifica
            # la conversación en una lista futura.
            if not previos:
                cursor.execute("""
                    UPDATE agente_conversaciones SET titulo = %s
                    WHERE id = %s AND titulo IS NULL
                """, (texto[:80], conv_id))

    except Exception as e:
        import traceback
        print('[AGENTE] Error atendiendo el mensaje:')
        traceback.print_exc()
        return jsonify({'success': False,
                        'error': 'El asistente no pudo responder. '
                                 'Intenta de nuevo.'}), 500

    return jsonify({
        'success':         True,
        'conversacion_id': conv_id,
        'mensaje_id':      mensaje_id,
        'contenido':       respuesta,
        'propuesta':       propuesta,
        'avisos':          avisos,
    })


@agente_bp.route('/crear', methods=['POST'])
def crear_formulario():
    """Confirma una propuesta: crea los campos nuevos y el formulario."""
    error = _solo_admin()
    if error:
        return error

    from app import db_connection
    mensaje_id = (request.get_json(silent=True) or {}).get('mensaje_id')
    if not mensaje_id:
        return jsonify({'success': False, 'error': 'Falta la propuesta.'}), 400

    empresa_id = session.get('empresa_id')

    try:
        with db_connection() as (conn, cursor):
            # La propuesta se lee de la base, no de lo que mande el
            # navegador: así nadie puede alterarla en el camino.
            cursor.execute("""
                SELECT m.propuesta
                FROM agente_mensajes m
                JOIN agente_conversaciones c ON c.id = m.conversacion_id
                WHERE m.id = %s AND c.empresa_id = %s AND c.user_id = %s
            """, (mensaje_id, empresa_id, session.get('user_id')))
            fila = cursor.fetchone()

            if not fila or not fila[0]:
                return jsonify({'success': False,
                                'error': 'Esa propuesta no existe.'}), 404

            try:
                resultado = crear_desde_propuesta(cursor, empresa_id,
                                                  session.get('user_id'),
                                                  fila[0])
            except ValueError as e:
                return jsonify({'success': False, 'error': str(e)}), 400

    except Exception as e:
        import traceback
        print('[AGENTE] Error creando el formulario:')
        traceback.print_exc()
        return jsonify({'success': False,
                        'error': 'No se pudo crear el formulario.'}), 500

    return jsonify(dict({'success': True}, **resultado))