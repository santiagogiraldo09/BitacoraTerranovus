# -*- coding: utf-8 -*-
"""
clasificacion.py — Asignación de temas a las observaciones de campo.

Cada respuesta de formulario se lee una sola vez: el modelo escoge, de un
catálogo cerrado, qué temas trata su texto libre. El resultado queda en
`registro_temas` y nunca se vuelve a calcular.

El catálogo es cerrado a propósito. Si el modelo inventara la etiqueta en
cada llamada, el mismo problema descrito con otras palabras saldría con
nombres distintos y los conteos del panel —que es todo lo que el panel
hace— no significarían nada.

Dos caminos de entrada, ambos sobre las mismas funciones:

  lanzar_clasificacion()  hilo en segundo plano, tras guardar un registro.
                          El usuario de campo no espera por él.
  barrer_pendientes()     repaso periódico. Recoge lo que el hilo no
                          alcanzó a procesar (reinicio de worker, falla de
                          OpenAI, timeout) y sirve para el backfill.

La base de datos es la cola: el estado vive en
`respuestas_formulario.clasificacion_estado`, no en el hilo. Si el proceso
muere, la fila sigue marcada y alguien la recoge después.
"""

import os
import json
import time
import logging
import threading

from openai import OpenAI

log = logging.getLogger(__name__)

# ── Configuración ────────────────────────────────────────────────────────
MODELO          = 'gpt-4o-mini'
TEMPERATURA     = 0.1          # la clasificación debe ser estable, no creativa
TIMEOUT         = 30
LOTE            = 20           # textos por llamada
MAX_CHARS_TEXTO = 1500         # recorte por texto, acota el prompt
MAX_TEMAS       = 3            # temas por registro
HILOS_MAX       = 3            # clasificaciones simultáneas (memoria en Render)
MINUTOS_ZOMBIE  = 15           # 'procesando' más viejo que esto se recupera

# Sólo el multi-línea: el texto corto suele ser un nombre o un código,
# no una observación, y sólo agregaría ruido.
TIPOS_TEXTO = {'texto_largo'}

SEVERIDADES = {'informativo', 'atencion', 'critico'}

_semaforo = threading.Semaphore(HILOS_MAX)
_cliente  = None


def _openai():
    """Cliente perezoso: no se construye al importar el módulo."""
    global _cliente
    if _cliente is None:
        _cliente = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    return _cliente


# ══════════════════════════════════════════════════════════════════════════
#  CATÁLOGO
# ══════════════════════════════════════════════════════════════════════════

def catalogo_temas(cursor, empresa_id, sector='construccion'):
    """Temas base de IAC (empresa_id NULL) más los propios de la empresa.

    Si la empresa definió un código que ya existe en la base, el suyo manda.
    """
    cursor.execute("""
        SELECT codigo, nombre, descripcion, empresa_id
        FROM temas_catalogo
        WHERE activo = TRUE
          AND sector = %s
          AND (empresa_id IS NULL OR empresa_id = %s)
        ORDER BY empresa_id NULLS FIRST, orden
    """, (sector, empresa_id))

    temas = {}
    for codigo, nombre, descripcion, emp in cursor.fetchall():
        temas[codigo] = {'codigo': codigo, 'nombre': nombre,
                         'descripcion': descripcion}
    return list(temas.values())


# ══════════════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DEL TEXTO
# ══════════════════════════════════════════════════════════════════════════

def _texto_de_respuesta(respuestas, campos):
    """Junta las observaciones largas de un registro en un solo texto.

    Incluye los campos dentro de grupos repetibles, que viven bajo
    `__repeticiones[gid]` y pueden aportar varios valores por registro.
    """
    if not isinstance(respuestas, dict):
        return ''

    ids_texto = {c['id']: c['nombre'] for c in campos
                 if c.get('tipo') in TIPOS_TEXTO}
    if not ids_texto:
        return ''

    partes = []

    # Campos sueltos
    for cid, nombre in ids_texto.items():
        valor = respuestas.get(cid) or respuestas.get(str(cid))
        if isinstance(valor, str) and valor.strip():
            partes.append(f"{nombre}: {valor.strip()}")

    # Campos dentro de grupos repetibles
    repeticiones = respuestas.get('__repeticiones') or {}
    if isinstance(repeticiones, dict):
        for bloques in repeticiones.values():
            if not isinstance(bloques, list):
                continue
            for bloque in bloques:
                if not isinstance(bloque, dict):
                    continue
                for cid, nombre in ids_texto.items():
                    valor = bloque.get(cid) or bloque.get(str(cid))
                    if isinstance(valor, str) and valor.strip():
                        partes.append(f"{nombre}: {valor.strip()}")

    texto = ' | '.join(partes)
    return texto[:MAX_CHARS_TEXTO]


# ══════════════════════════════════════════════════════════════════════════
#  PROMPT
# ══════════════════════════════════════════════════════════════════════════

def _prompt(temas):
    lista = '\n'.join(
        f"- {t['codigo']}: {t['nombre']}. {t['descripcion']}" for t in temas
    )
    return f"""Eres un clasificador de observaciones de obra. Tu única tarea es
asignar temas de una lista cerrada. No resumes, no interpretas, no opinas.

TEMAS DISPONIBLES
{lista}

REGLAS ABSOLUTAS
1. Sólo puedes usar códigos de la lista anterior. Nunca inventes uno.
2. Asigna como máximo {MAX_TEMAS} temas por texto, sólo los que el texto
   menciona de forma explícita. No infieras causas ni consecuencias.
3. El "fragmento" debe ser una cita literal del texto, copiada tal cual,
   de máximo 120 caracteres. Nunca lo redactes tú.
4. Si el texto no reporta ninguna situación analizable (por ejemplo
   "todo normal", "sin novedades", o sólo describe avance sin incidencias),
   usa únicamente el tema "sin_tema" y deja "fragmento" vacío.
5. Si el texto sí reporta algo relevante pero ningún tema encaja, usa
   "otro" y escribe en "propuesto" el nombre corto que tendría ese tema
   (2 a 4 palabras, en español). En cualquier otro caso "propuesto" es null.
6. "severidad" sólo puede ser: informativo, atencion, critico.
   - critico: hay daño, lesión, o una detención en curso.
   - atencion: hay un problema que requiere acción pero no es crítico.
   - informativo: se menciona el tema sin que haya problema activo.
7. "confianza" es un número entre 0 y 1 que refleja qué tan claro es el
   texto. Un texto ambiguo o muy corto va por debajo de 0.6.

FORMATO DE SALIDA
Devuelve exactamente un objeto JSON con esta estructura, un elemento por
cada texto recibido, conservando el índice "i" que se te entregó:

{{"resultados": [
  {{"i": 0,
    "temas": [
      {{"codigo": "seguridad", "severidad": "atencion",
        "confianza": 0.9, "fragmento": "dos operarios sin casco"}}
    ],
    "propuesto": null}}
]}}"""


# ══════════════════════════════════════════════════════════════════════════
#  LLAMADA AL MODELO
# ══════════════════════════════════════════════════════════════════════════

def _clasificar_lote(textos, temas):
    """textos: lista de (indice, texto). Devuelve {indice: resultado}.

    Ante cualquier fallo devuelve {} — quien llame deja las filas en
    'pendiente' para que el barrido las reintente.
    """
    if not textos:
        return {}

    entrada = '\n\n'.join(
        f"[{i}]\n{t}" for i, t in textos
    )
    codigos = {t['codigo'] for t in temas}

    try:
        resp = _openai().chat.completions.create(
            model=MODELO,
            temperature=TEMPERATURA,
            timeout=TIMEOUT,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _prompt(temas)},
                {"role": "user",   "content": entrada},
            ],
        )
        datos = json.loads(resp.choices[0].message.content or '{}')
        uso   = resp.usage
    except Exception as e:
        log.warning("clasificacion: falló la llamada al modelo: %s", e)
        return {}

    salida = {}
    for item in (datos.get('resultados') or []):
        if not isinstance(item, dict):
            continue
        try:
            i = int(item.get('i'))
        except (TypeError, ValueError):
            continue

        limpios = []
        for t in (item.get('temas') or [])[:MAX_TEMAS]:
            if not isinstance(t, dict):
                continue
            codigo = str(t.get('codigo') or '').strip()
            if codigo not in codigos:       # el modelo inventó uno: se descarta
                continue
            sev = str(t.get('severidad') or '').strip().lower()
            try:
                conf = round(float(t.get('confianza')), 2)
            except (TypeError, ValueError):
                conf = None
            limpios.append({
                'codigo':    codigo,
                'severidad': sev if sev in SEVERIDADES else None,
                'confianza': min(max(conf, 0), 1) if conf is not None else None,
                'fragmento': (str(t.get('fragmento') or '').strip() or None),
            })

        propuesto = item.get('propuesto')
        propuesto = str(propuesto).strip()[:80] if propuesto else None

        salida[i] = {'temas': limpios, 'propuesto': propuesto}

    log.info("clasificacion: %d textos, %d entrada / %d salida tokens",
             len(textos), uso.prompt_tokens, uso.completion_tokens)
    return salida


# ══════════════════════════════════════════════════════════════════════════
#  PROCESO PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════

def _reclamar(cursor, ids=None, limite=None):
    """Marca filas como 'procesando' y devuelve las que logró reclamar.

    Es lo que evita que el hilo y el barrido trabajen sobre el mismo
    registro: sólo una de las dos transacciones gana el UPDATE.
    """
    if ids:
        cursor.execute("""
            UPDATE respuestas_formulario
            SET clasificacion_estado = 'procesando', clasificado_en = NOW()
            WHERE id = ANY(%s) AND clasificacion_estado = 'pendiente'
            RETURNING id
        """, (list(ids),))
    else:
        cursor.execute("""
            UPDATE respuestas_formulario
            SET clasificacion_estado = 'procesando', clasificado_en = NOW()
            WHERE id IN (
                SELECT id FROM respuestas_formulario
                WHERE clasificacion_estado = 'pendiente'
                ORDER BY id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id
        """, (limite,))
    return [r[0] for r in cursor.fetchall()]


def _recuperar_zombies(cursor):
    """Devuelve a 'pendiente' lo que quedó colgado en 'procesando'.

    Pasa cuando el worker se reinicia a mitad de una clasificación. Sin
    esto, esas filas no las vuelve a mirar nadie.
    """
    cursor.execute("""
        UPDATE respuestas_formulario
        SET clasificacion_estado = 'pendiente'
        WHERE clasificacion_estado = 'procesando'
          AND clasificado_en < NOW() - INTERVAL '%s minutes'
    """ % MINUTOS_ZOMBIE)
    return cursor.rowcount


def clasificar_ids(ids):
    """Clasifica un conjunto de respuestas ya reclamadas. Devuelve cuántas.

    Agrupa por formulario para leer la definición de campos una sola vez,
    y fija el tenant por empresa antes de insertar, porque las políticas
    RLS de registro_temas lo exigen también en el INSERT.
    """
    from app import db_connection, _definicion_campos

    if not ids:
        return 0

    procesadas = 0

    with db_connection() as (conn, cursor):
        cursor.execute("""
            SELECT rf.id, rf.formulario_id, rf.id_proyecto,
                   rf.respuestas, rf.created_at::date, p.empresa_id
            FROM respuestas_formulario rf
            JOIN proyectos p ON p.id = rf.id_proyecto
            WHERE rf.id = ANY(%s)
        """, (list(ids),))
        filas = cursor.fetchall()

        # (empresa_id, formulario_id) -> [(id, proyecto_id, respuestas, fecha)]
        grupos = {}
        for rid, form_id, proy_id, respuestas, fecha, emp_id in filas:
            grupos.setdefault((emp_id, form_id), []).append(
                (rid, proy_id, respuestas, fecha))

        for (emp_id, form_id), registros in grupos.items():
            # Sin sesión de Flask, db_connection deja app.empresa_id en 1.
            # Hay que fijarlo con el valor real antes de leer o escribir.
            cursor.execute("SET app.empresa_id = %s", (str(emp_id),))

            try:
                _, campos = _definicion_campos(cursor, emp_id, form_id)
            except Exception as e:
                log.warning("clasificacion: sin definición de campos para "
                            "formulario %s: %s", form_id, e)
                campos = []

            temas = catalogo_temas(cursor, emp_id)
            if not temas:
                log.warning("clasificacion: empresa %s sin catálogo", emp_id)
                _marcar(cursor, [r[0] for r in registros], 'error',
                        'sin catálogo de temas')
                conn.commit()
                continue

            # Preparar textos; los vacíos se resuelven sin llamar al modelo
            pendientes, vacios = [], []
            indice = {}
            for rid, proy_id, respuestas, fecha in registros:
                texto = _texto_de_respuesta(respuestas, campos)
                if texto:
                    i = len(indice)
                    indice[i] = (rid, proy_id, fecha)
                    pendientes.append((i, texto))
                else:
                    vacios.append((rid, proy_id, fecha))

            for rid, proy_id, fecha in vacios:
                _guardar_temas(cursor, emp_id, rid, proy_id, form_id, fecha,
                               [{'codigo': 'sin_tema', 'severidad': None,
                                 'confianza': None, 'fragmento': None}], None)
                _marcar(cursor, [rid], 'listo')
                procesadas += 1

            # Lotes
            for inicio in range(0, len(pendientes), LOTE):
                tanda     = pendientes[inicio:inicio + LOTE]
                resultado = _clasificar_lote(tanda, temas)

                if not resultado:
                    # Falló el modelo: se devuelven a la cola, sin perderlas
                    _marcar(cursor, [indice[i][0] for i, _ in tanda],
                            'pendiente')
                    conn.commit()
                    continue

                for i, _ in tanda:
                    rid, proy_id, fecha = indice[i]
                    datos = resultado.get(i)
                    if not datos:
                        _marcar(cursor, [rid], 'pendiente')
                        continue
                    _guardar_temas(cursor, emp_id, rid, proy_id, form_id,
                                   fecha, datos['temas'], datos['propuesto'])
                    _marcar(cursor, [rid], 'listo')
                    procesadas += 1

                conn.commit()

            conn.commit()

    return procesadas


def _guardar_temas(cursor, empresa_id, respuesta_id, proyecto_id,
                   formulario_id, fecha, temas, propuesto):
    """Reemplaza los temas de un registro. Idempotente: borra y reinserta."""
    cursor.execute("DELETE FROM registro_temas WHERE respuesta_id = %s",
                   (respuesta_id,))

    if not temas:
        temas = [{'codigo': 'sin_tema', 'severidad': None,
                  'confianza': None, 'fragmento': None}]

    for t in temas:
        cursor.execute("""
            INSERT INTO registro_temas
                (empresa_id, respuesta_id, proyecto_id, formulario_id,
                 tema_codigo, severidad, confianza, fragmento,
                 tema_propuesto, fecha_dato)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (respuesta_id, tema_codigo) DO NOTHING
        """, (empresa_id, respuesta_id, proyecto_id, formulario_id,
              t['codigo'], t['severidad'], t['confianza'], t['fragmento'],
              propuesto if t['codigo'] == 'otro' else None, fecha))


def _marcar(cursor, ids, estado, error=None):
    cursor.execute("""
        UPDATE respuestas_formulario
        SET clasificacion_estado = %s,
            clasificado_en       = NOW(),
            clasificacion_error  = %s
        WHERE id = ANY(%s)
    """, (estado, error, list(ids)))


# ══════════════════════════════════════════════════════════════════════════
#  ENTRADAS PÚBLICAS
# ══════════════════════════════════════════════════════════════════════════

def lanzar_clasificacion(respuesta_id):
    """Clasifica en segundo plano, sin hacer esperar al usuario.

    Se llama justo después de responder un guardado. Si el hilo muere o el
    worker se reinicia, la fila queda en 'pendiente' o se recupera como
    zombie, y el barrido la recoge.
    """
    def _trabajo():
        if not _semaforo.acquire(blocking=False):
            # Demasiadas clasificaciones a la vez: que la recoja el barrido
            log.info("clasificacion: semáforo lleno, %s queda pendiente",
                     respuesta_id)
            return
        try:
            from app import db_connection
            with db_connection() as (conn, cursor):
                ids = _reclamar(cursor, ids=[respuesta_id])
                conn.commit()
            if ids:
                clasificar_ids(ids)
        except Exception as e:
            log.exception("clasificacion: error en hilo para %s: %s",
                          respuesta_id, e)
        finally:
            _semaforo.release()

    threading.Thread(target=_trabajo, daemon=True,
                     name=f"clasif-{respuesta_id}").start()


def barrer_pendientes(limite=200):
    """Recoge lo que quedó sin clasificar. Para el cron y el backfill.

    Devuelve (procesadas, recuperadas).
    """
    from app import db_connection

    with db_connection() as (conn, cursor):
        recuperadas = _recuperar_zombies(cursor)
        ids         = _reclamar(cursor, limite=limite)
        conn.commit()

    if not ids:
        return 0, recuperadas

    inicio     = time.time()
    procesadas = clasificar_ids(ids)
    log.info("clasificacion: %d procesadas en %.1fs (%d recuperadas)",
             procesadas, time.time() - inicio, recuperadas)
    return procesadas, recuperadas
