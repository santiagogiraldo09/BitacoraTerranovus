# -*- coding: utf-8 -*-
"""
resumen_ia.py — Widget de análisis con IA para los tableros.

El usuario asocia el widget a un formulario y el sistema saca lo que
haya de valioso en sus respuestas, sin que nadie defina de antemano qué
buscar.

Cómo se reparte el trabajo:

  Python calcula    Todo número que se muestre. Conteos por categoría,
                    sumas y promedios por campo numérico, y los cruces
                    entre ambos — "cuánto material por proveedor".
  El modelo decide  Qué de todo eso vale la pena contar, en qué orden, y
                    cómo se lee en lenguaje de obra.

Esa división es deliberada: un modelo haciendo aritmética sobre cientos
de filas se equivoca, y una cifra equivocada en un tablero destruye la
confianza en todo lo demás. Aquí el modelo nunca calcula: escoge e
interpreta cifras que ya vienen hechas.
"""

import os
import json
import hashlib
from datetime import datetime

from flask import Blueprint, request, jsonify, session, current_app

resumen_bp = Blueprint('resumen_ia', __name__)

MODELO       = 'gpt-4o-mini'
TIMEOUT      = 40
MAX_REGISTROS = 2000     # tope de filas que se perfilan
TOP_VALORES   = 8        # valores distintos que se listan por categoría
MAX_CATEG     = 5        # campos categóricos que entran al perfil
MAX_NUM       = 4        # campos numéricos que entran al perfil
MAX_TEXTOS    = 12       # observaciones de ejemplo que lee el modelo

TIPOS_CATEG  = {'seleccion', 'seleccion_unica', 'seleccion_dinamica'}
TIPOS_NUM    = {'numero', 'moneda', 'porcentaje'}
TIPOS_TEXTO  = {'texto_largo', 'texto_corto'}
MIN_TEXTO    = 40        # un texto corto sólo cuenta si trae narrativa


# ══════════════════════════════════════════════════════════════════════
#  LECTURA DE LAS RESPUESTAS
# ══════════════════════════════════════════════════════════════════════

def _filas_planas(respuestas, campos):
    """Convierte un registro en una o varias filas {campo_id: valor}.

    Un campo suelto aporta un valor por registro. Los campos dentro de
    un grupo repetible aportan uno por bloque, así que un registro con
    tres materiales produce tres filas — que es justo lo que hace falta
    para cruzar 'material' contra 'cantidad'.
    """
    if not isinstance(respuestas, dict):
        return []

    base = {}
    for c in campos:
        if c.get('gid'):
            continue
        v = respuestas.get(c['id'])
        if v is None:
            v = respuestas.get(str(c['id']))
        if v not in (None, ''):
            base[c['id']] = v

    repeticiones = respuestas.get('__repeticiones') or {}
    filas = []
    if isinstance(repeticiones, dict):
        for bloques in repeticiones.values():
            if not isinstance(bloques, list):
                continue
            for bloque in bloques:
                if not isinstance(bloque, dict):
                    continue
                fila = dict(base)
                for c in campos:
                    if not c.get('gid'):
                        continue
                    v = bloque.get(c['id']) or bloque.get(str(c['id']))
                    if v not in (None, ''):
                        fila[c['id']] = v
                filas.append(fila)

    return filas or [base]


def _numero(v):
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    t = v.strip().replace('$', '').replace('%', '').replace(' ', '')
    if not t:
        return None
    # 1.234,56 (es) frente a 1,234.56 (en)
    if ',' in t and '.' in t:
        t = t.replace('.', '').replace(',', '.') if t.rfind(',') > t.rfind('.') \
            else t.replace(',', '')
    elif ',' in t:
        t = t.replace(',', '.')
    try:
        return float(t)
    except ValueError:
        return None


def _etiqueta(v):
    """Texto visible de un valor de selección."""
    if isinstance(v, dict):
        return str(v.get('nombre') or v.get('izquierda')
                   or v.get('valor') or '').strip()
    if isinstance(v, list):
        return ', '.join(_etiqueta(x) for x in v if x)
    return str(v).strip()


# ══════════════════════════════════════════════════════════════════════
#  PERFIL: TODO LO QUE SE CALCULA EN PYTHON
# ══════════════════════════════════════════════════════════════════════

def _perfil(campos, filas):
    """Retrato numérico del formulario. Ningún valor de aquí lo produce
    el modelo: son los que después puede citar."""

    categoricos = [c for c in campos if c.get('tipo') in TIPOS_CATEG][:MAX_CATEG]
    numericos   = [c for c in campos if c.get('tipo') in TIPOS_NUM][:MAX_NUM]
    textuales   = [c for c in campos if c.get('tipo') in TIPOS_TEXTO]

    perfil = {'total_filas': len(filas), 'campos': [], 'cruces': [], 'textos': []}

    # ── Campos de selección: distribución de valores ──
    for c in categoricos:
        conteo = {}
        for f in filas:
            e = _etiqueta(f.get(c['id']))
            if e:
                conteo[e] = conteo.get(e, 0) + 1
        if not conteo:
            continue
        orden = sorted(conteo.items(), key=lambda x: -x[1])[:TOP_VALORES]
        perfil['campos'].append({
            'nombre':      c['nombre'],
            'tipo':        'categoria',
            'respondidos': sum(conteo.values()),
            'distintos':   len(conteo),
            'valores':     [{'valor': v, 'veces': n} for v, n in orden],
        })

    # ── Campos numéricos: totales ──
    for c in numericos:
        vals = [n for n in (_numero(f.get(c['id'])) for f in filas) if n is not None]
        if not vals:
            continue
        perfil['campos'].append({
            'nombre':      c['nombre'],
            'tipo':        'numero',
            'respondidos': len(vals),
            'suma':        round(sum(vals), 2),
            'promedio':    round(sum(vals) / len(vals), 2),
            'minimo':      round(min(vals), 2),
            'maximo':      round(max(vals), 2),
        })

    # ── Cruces: cada categoría contra cada número ──
    #   Es lo que responde "cuánta cantidad por material". Se calcula
    #   aquí y no en el modelo, que con cien filas se equivocaría.
    for cat in categoricos:
        for num in numericos:
            grupos = {}
            for f in filas:
                e = _etiqueta(f.get(cat['id']))
                n = _numero(f.get(num['id']))
                if not e or n is None:
                    continue
                g = grupos.setdefault(e, {'suma': 0.0, 'veces': 0})
                g['suma']  += n
                g['veces'] += 1
            if len(grupos) < 2:
                continue
            orden = sorted(grupos.items(), key=lambda x: -x[1]['suma'])[:TOP_VALORES]
            perfil['cruces'].append({
                'agrupado_por': cat['nombre'],
                'medida':       num['nombre'],
                'filas': [{'valor': v,
                           'suma': round(d['suma'], 2),
                           'veces': d['veces'],
                           'promedio': round(d['suma'] / d['veces'], 2)}
                          for v, d in orden],
            })

    # ── Observaciones de ejemplo ──
    for c in textuales:
        for f in filas:
            v = f.get(c['id'])
            if not isinstance(v, str):
                continue
            t = v.strip()
            if len(t) < MIN_TEXTO:
                continue
            perfil['textos'].append({'campo': c['nombre'], 'texto': t[:400]})
            if len(perfil['textos']) >= MAX_TEXTOS:
                break
        if len(perfil['textos']) >= MAX_TEXTOS:
            break

    return perfil


# ══════════════════════════════════════════════════════════════════════
#  EL MODELO
# ══════════════════════════════════════════════════════════════════════

PROMPT = """Eres un analista que lee datos de campo de una obra o
proyecto y le explica a un gerente qué está pasando.

Recibes el PERFIL ya calculado de un formulario: distribuciones, totales
y cruces entre campos. Todos esos números son correctos y definitivos.

REGLAS ABSOLUTAS
1. NUNCA calcules. No sumes, no restes, no estimes porcentajes que no
   estén en el perfil. Si quieres citar una cifra, cópiala tal cual.
2. NUNCA inventes causas. Si algo llama la atención, dilo como
   observación, no como explicación. "Tres proveedores concentran el
   80% del volumen" sí; "por eso hay retrasos" no.
3. Si los datos no alcanzan para decir algo útil, dilo con franqueza en
   vez de rellenar.
4. No nombres personas en términos negativos.
5. Habla en español de obra, directo, sin adornos ni palabras como
   "cabe destacar" o "es importante mencionar".

QUÉ BUSCAR
Lo que un gerente no vería de un vistazo: concentraciones (pocos valores
que acaparan el total), desbalances, valores atípicos, campos que casi
nadie llena, y lo que digan las observaciones escritas.

FORMATO DE SALIDA
Un objeto JSON, nada más:

{
  "titular": "una frase de máximo 90 caracteres con lo más importante",
  "metricas": [
    {"etiqueta": "Total de registros", "valor": "85"},
    {"etiqueta": "Material más usado", "valor": "Concreto 3000 PSI"}
  ],
  "hallazgos": [
    "Frase completa con una observación concreta y su cifra.",
    "Otra observación."
  ],
  "limitacion": "qué no se puede concluir con estos datos, o null"
}

Entre 2 y 4 métricas, entre 2 y 5 hallazgos. Los valores de "metricas"
se copian del perfil, nunca se calculan."""


def _analizar(perfil, nombre_formulario):
    from openai import OpenAI
    cliente = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    entrada = (f"Formulario: {nombre_formulario}\n\n"
               f"PERFIL (cifras ya calculadas, son correctas):\n"
               f"{json.dumps(perfil, ensure_ascii=False, indent=1)}")

    resp = cliente.chat.completions.create(
        model=MODELO,
        temperature=0.3,
        timeout=TIMEOUT,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user",   "content": entrada}],
    )
    return json.loads(resp.choices[0].message.content or '{}')


# ══════════════════════════════════════════════════════════════════════
#  ENDPOINT
# ══════════════════════════════════════════════════════════════════════

@resumen_bp.route('/api/bi/resumen-ia', methods=['POST'])
def bi_resumen_ia():
    from app import db_connection, _definicion_campos

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data          = request.get_json() or {}
    empresa_id    = session.get('empresa_id')
    formulario_id = data.get('formulario_id')
    viz_id        = data.get('visualizacion_id')
    filtros       = data.get('filtros') or {}

    if not formulario_id:
        return jsonify({'sin_datos': True,
                        'texto': 'Esta tarjeta no tiene formulario asociado.'})

    try:
        with db_connection() as (conn, cursor):
            nombre_form, campos = _definicion_campos(cursor, empresa_id,
                                                     int(formulario_id))
            if not campos:
                return jsonify({'sin_datos': True,
                                'texto': 'El formulario no tiene campos definidos.'})

            # Las respuestas se acotan por empresa a través del proyecto:
            # respuestas_formulario no tiene empresa_id propio.
            sql = """
                SELECT rf.respuestas
                FROM respuestas_formulario rf
                JOIN proyectos p ON p.id = rf.id_proyecto
                WHERE rf.formulario_id = %s AND p.empresa_id = %s
            """
            params = [int(formulario_id), empresa_id]

            if filtros.get('proyecto_id'):
                sql += " AND rf.id_proyecto = %s"
                params.append(filtros['proyecto_id'])
            if filtros.get('desde'):
                sql += " AND rf.created_at >= %s"
                params.append(filtros['desde'])
            if filtros.get('hasta'):
                sql += " AND rf.created_at < (%s::date + 1)"
                params.append(filtros['hasta'])

            sql += " ORDER BY rf.created_at DESC LIMIT %s"
            params.append(MAX_REGISTROS)

            cursor.execute(sql, params)
            registros = [r[0] for r in cursor.fetchall()]

            if not registros:
                return jsonify({'sin_datos': True,
                                'texto': 'Este formulario no tiene respuestas '
                                         'en el periodo filtrado.'})

            filas  = []
            for r in registros:
                filas.extend(_filas_planas(r, campos))
            perfil = _perfil(campos, filas)
            perfil['registros'] = len(registros)

            huella = hashlib.sha256(
                json.dumps(perfil, sort_keys=True, default=str).encode('utf-8')
            ).hexdigest()

            if viz_id:
                cursor.execute("""
                    UPDATE notas_ia SET ultimo_uso = NOW()
                    WHERE visualizacion_id = %s AND hash_datos = %s
                    RETURNING contenido
                """, (viz_id, huella))
                guardada = cursor.fetchone()
                conn.commit()
                if guardada:
                    salida = guardada[0] or {}
                    salida['cacheado'] = True
                    return jsonify(salida)

        # Fuera del with: la llamada al modelo no necesita la base y
        # mantener la conexión abierta mientras tanto la desperdicia.
        analisis = _analizar(perfil, nombre_form)
        analisis['registros'] = len(registros)

        if viz_id:
            try:
                with db_connection() as (conn, cursor):
                    cursor.execute("""
                        INSERT INTO notas_ia
                            (empresa_id, visualizacion_id, hash_datos,
                             contenido, modelo)
                        VALUES (%s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (visualizacion_id, hash_datos) DO NOTHING
                    """, (empresa_id, viz_id, huella,
                          json.dumps(analisis, ensure_ascii=False), MODELO))
                    conn.commit()
            except Exception as e:
                current_app.logger.warning(f"[BI-IA] no se pudo cachear: {e}")

        analisis['cacheado'] = False
        return jsonify(analisis)

    except Exception as e:
        current_app.logger.exception('bi_resumen_ia')
        return jsonify({'error': str(e)}), 500
