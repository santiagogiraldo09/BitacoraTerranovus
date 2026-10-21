# -*- coding: utf-8 -*-
"""
resumen_ia.py — Widget "Hallazgos con IA" para los tableros.

El usuario asocia el widget a un formulario y el sistema infiere lo que
haya de valioso en sus respuestas, sin que nadie defina de antemano qué
buscar.

Cómo se reparte el trabajo:

  Python calcula    Todo número que se muestre: distribuciones,
                    concentración, totales, cruces entre campos,
                    coincidencias entre categorías y actividad en el
                    tiempo.
  El modelo decide  Qué significa cada campo —para eso lee su nombre—,
                    qué de todo eso vale la pena contar, y qué bloque
                    merece graficarse.

Esa división es deliberada. Un modelo haciendo aritmética sobre cientos
de filas se equivoca, y una cifra mal sumada en un tablero destruye la
confianza en todo lo demás. Aquí el modelo nunca calcula: ni siquiera
las barras que escoge mostrar, cuyos valores le adjunta el servidor
después de que él nombra el bloque.

Y al revés: NINGÚN campo se descarta por su tipo. Un teléfono, una
fecha, una foto y una observación entran todos al perfil con el mismo
derecho. Que un campo numérico sea en realidad una matrícula no lo
decide una lista de nombres prohibidos —frágil y en un solo idioma—
sino el dato de cuántos de sus valores son distintos, que viaja en el
perfil para que el modelo lo juzgue leyendo el nombre del campo.
"""

import os
import json
import hashlib

from flask import Blueprint, request, jsonify, session, current_app

resumen_bp = Blueprint('resumen_ia', __name__)

MODELO        = 'gpt-4o-mini'
TIMEOUT       = 45
MAX_REGISTROS = 2000     # tope de respuestas que se perfilan
TOP_VALORES   = 8        # valores frecuentes que se listan por campo
MAX_TEXTOS    = 60       # observaciones de ejemplo que lee el modelo
MAX_LARGO     = 400      # recorte de cada observación

# Un campo sirve para agrupar si tiene pocos valores distintos.
MAX_DISTINTOS_GRUPO = 25
# Si casi todos los valores son distintos, el campo identifica filas en
# vez de medirlas. No se oculta: se marca.
RATIO_IDENTIFICADOR = 0.9


# ══════════════════════════════════════════════════════════════════════
#  LECTURA DE LAS RESPUESTAS
# ══════════════════════════════════════════════════════════════════════

def _filas_planas(respuestas, campos, fecha=None):
    """Convierte un registro en una o varias filas {campo_id: valor}.

    Un campo suelto aporta un valor por registro. Los campos dentro de
    un grupo repetible aportan uno por bloque, así que un registro con
    tres materiales produce tres filas — que es justo lo que hace falta
    para cruzar 'material' contra 'cantidad'.
    """
    if not isinstance(respuestas, dict):
        return []

    base = {'__fecha': fecha}
    for c in campos:
        if c.get('gid'):
            continue
        v = respuestas.get(c['id'])
        if v is None:
            v = respuestas.get(str(c['id']))
        if v not in (None, '', []):
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
                    if v not in (None, '', []):
                        fila[c['id']] = v
                filas.append(fila)

    return filas or [base]


def _texto(v):
    """Representación visible de cualquier valor."""
    if v is None:
        return ''
    if isinstance(v, dict):
        return str(v.get('nombre') or v.get('izquierda')
                   or v.get('valor') or '').strip()
    if isinstance(v, list):
        return ', '.join(t for t in (_texto(x) for x in v) if t)
    if isinstance(v, bool):
        return 'Sí' if v else 'No'
    return str(v).strip()


def _numero(v):
    """El valor como número, si lo admite. No juzga si tiene sentido
    sumarlo: eso lo decide quien lea el perfil."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    t = v.strip().replace('$', '').replace('%', '').replace(' ', '')
    if not t:
        return None
    if ',' in t and '.' in t:
        t = (t.replace('.', '').replace(',', '.')
             if t.rfind(',') > t.rfind('.') else t.replace(',', ''))
    elif ',' in t:
        t = t.replace(',', '.')
    try:
        return float(t)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════
#  PERFIL
# ══════════════════════════════════════════════════════════════════════

def _resumen_campo(campo, filas):
    """Retrato de un campo, sea del tipo que sea.

    Todo campo recibe el mismo trato base: cuántos lo respondieron,
    cuántos valores distintos tiene, cuáles se repiten más y qué tanto
    se concentra el total en unos pocos. Encima se agrega lo que el
    contenido permita —totales si es numérico, rango si son fechas—
    sin que el tipo declarado excluya a nadie.
    """
    crudos = [f.get(campo['id']) for f in filas]
    textos = [t for t in (_texto(v) for v in crudos) if t]
    if not textos:
        return None

    conteo = {}
    for t in textos:
        conteo[t] = conteo.get(t, 0) + 1
    frecuentes = sorted(conteo.items(), key=lambda x: -x[1])[:TOP_VALORES]

    distintos = len(conteo)
    ratio     = distintos / len(textos)

    r = {
        'nombre':      campo['nombre'],
        'tipo':        campo.get('tipo') or 'desconocido',
        'grupo':       campo.get('grupo') or None,
        'respondidos': len(textos),
        'de_filas':    len(filas),
        'distintos':   distintos,
        # La señal que permite distinguir una medida de una matrícula.
        'casi_todos_distintos': ratio > RATIO_IDENTIFICADOR and len(textos) >= 10,
        'valores_frecuentes': [{'valor': v[:120], 'veces': n}
                               for v, n in frecuentes],
    }

    # Concentración: lo que convierte "hay varias empresas" en
    # "tres empresas acaparan el 62%".
    if distintos > 1:
        top3 = sum(n for _, n in frecuentes[:3])
        r['concentracion_top3_pct'] = round(100 * top3 / len(textos), 1)

    # Aritmética, cuando los valores la admiten
    numeros = [n for n in (_numero(v) for v in crudos) if n is not None]
    if numeros and len(numeros) >= len(textos) * 0.6:
        r['numerico'] = {
            'valores':  len(numeros),
            'suma':     round(sum(numeros), 2),
            'promedio': round(sum(numeros) / len(numeros), 2),
            'minimo':   round(min(numeros), 2),
            'maximo':   round(max(numeros), 2),
        }

    # Fechas: rango y concentración por mes
    es_fecha = (campo.get('tipo') == 'fecha'
                or all(len(t) >= 8 and t[:4].isdigit() for t in textos[:5]))
    if es_fecha:
        fechas = sorted(t for t in textos if len(t) >= 7 and t[:4].isdigit())
        if fechas:
            meses = {}
            for f in fechas:
                meses[f[:7]] = meses.get(f[:7], 0) + 1
            r['fechas'] = {
                'primera': fechas[0][:10],
                'ultima':  fechas[-1][:10],
                'por_mes': [{'mes': m, 'veces': n}
                            for m, n in sorted(meses.items())][-12:],
            }

    return r


def _cruces(agrupables, medibles, filas):
    """Suma de una medida por cada valor de una categoría."""
    salida = []
    for cat, _ in agrupables[:6]:
        for num, _ in medibles[:4]:
            if cat['id'] == num['id']:
                continue
            grupos = {}
            for f in filas:
                e = _texto(f.get(cat['id']))
                n = _numero(f.get(num['id']))
                if not e or n is None:
                    continue
                g = grupos.setdefault(e, {'suma': 0.0, 'veces': 0})
                g['suma']  += n
                g['veces'] += 1
            if len(grupos) < 2:
                continue
            orden = sorted(grupos.items(), key=lambda x: -x[1]['suma'])[:TOP_VALORES]
            salida.append({
                'agrupado_por': cat['nombre'],
                'medida':       num['nombre'],
                'filas': [{'valor': v[:120],
                           'suma': round(d['suma'], 2),
                           'veces': d['veces'],
                           'promedio': round(d['suma'] / d['veces'], 2)}
                          for v, d in orden],
            })
    return salida


def _coincidencias(agrupables, filas):
    """Qué valor de un campo tiende a aparecer junto a qué valor de otro.

    Es de donde sale la inferencia real en formularios sin cifras: qué
    interés predomina según el tipo de cliente, qué falla aparece en qué
    frente. Sólo se reporta cuando el predominio es claro, para no
    llenar el perfil de ruido.
    """
    salida = []
    for i, (a, ra) in enumerate(agrupables[:4]):
        for b, rb in agrupables[i + 1:5]:
            pares = {}
            for f in filas:
                va, vb = _texto(f.get(a['id'])), _texto(f.get(b['id']))
                if not va or not vb:
                    continue
                pares.setdefault(va, {})
                pares[va][vb] = pares[va].get(vb, 0) + 1
            if len(pares) < 2:
                continue

            filas_out = []
            for va, dist in sorted(pares.items(),
                                   key=lambda x: -sum(x[1].values()))[:6]:
                total = sum(dist.values())
                if total < 3:
                    continue
                vb, n = max(dist.items(), key=lambda x: x[1])
                pct = round(100 * n / total, 1)
                # Sin predominio no hay nada que inferir.
                if pct < 50:
                    continue
                filas_out.append({'valor': va[:120], 'registros': total,
                                  'predomina': vb[:120], 'pct': pct})

            if len(filas_out) >= 2:
                salida.append({'campo': a['nombre'], 'contra': b['nombre'],
                               'filas': filas_out})
    return salida


def _actividad(filas):
    """Registros por mes, a partir de la fecha de creación. Sirve para
    ver si la captura sube, baja o se concentró en pocos días."""
    meses = {}
    for f in filas:
        fecha = f.get('__fecha')
        if not fecha:
            continue
        clave = str(fecha)[:7]
        meses[clave] = meses.get(clave, 0) + 1
    if len(meses) < 2:
        return None
    orden = sorted(meses.items())[-12:]
    return {'por_mes': [{'mes': m, 'registros': n} for m, n in orden],
            'primer_mes': orden[0][0], 'ultimo_mes': orden[-1][0]}


def _perfil(campos, filas):
    """Retrato completo del formulario. Ningún número que salga en la
    tarjeta se produce fuera de aquí."""

    perfil = {'total_filas': len(filas), 'campos': [], 'cruces': [],
              'coincidencias': [], 'textos': [], 'actividad': None}

    resumenes = []
    for c in campos:
        r = _resumen_campo(c, filas)
        if r:
            resumenes.append((c, r))
            perfil['campos'].append(r)

    # Agrupa quien tiene pocos valores distintos; mide quien tiene
    # números que no son matrículas. El campo descartado como medida
    # sigue apareciendo completo arriba: aquí sólo se decide qué tiene
    # sentido sumar o cruzar.
    agrupables = [(c, r) for c, r in resumenes
                  if 1 < r['distintos'] <= MAX_DISTINTOS_GRUPO]
    medibles   = [(c, r) for c, r in resumenes
                  if 'numerico' in r and not r['casi_todos_distintos']]

    perfil['cruces']        = _cruces(agrupables, medibles, filas)
    perfil['coincidencias'] = _coincidencias(agrupables, filas)
    perfil['actividad']     = _actividad(filas)

    # Lo que la gente escribió. Cualquier campo puede traer prosa, sin
    # importar su tipo: lo que la delata es que sus valores sean largos
    # y poco repetidos.
    narrativos = [(c, r) for c, r in resumenes
                  if r['distintos'] > MAX_DISTINTOS_GRUPO]
    for c, _ in narrativos:
        for f in filas:
            t = _texto(f.get(c['id']))
            if len(t) < 25:
                continue
            perfil['textos'].append({'campo': c['nombre'], 'texto': t[:MAX_LARGO]})
            if len(perfil['textos']) >= MAX_TEXTOS:
                break
        if len(perfil['textos']) >= MAX_TEXTOS:
            break

    return perfil


# ══════════════════════════════════════════════════════════════════════
#  EL MODELO
# ══════════════════════════════════════════════════════════════════════

PROMPT = """Eres un analista que lee datos de campo de una obra o
proyecto y le dice a un gerente qué está pasando que él no había visto.

Recibes el PERFIL ya calculado de un formulario. Todos esos números son
correctos y definitivos.

CÓMO LEER EL PERFIL
El nombre de cada campo es lo que te dice qué significa. Un campo
llamado "Teléfono" con 357 valores distintos de 359 es una matrícula:
sus totales y su máximo no significan nada y no se citan. Uno llamado
"Cantidad" con 12 valores distintos es una medida: ahí sí. Usa el
nombre primero y el indicador "casi_todos_distintos" para confirmar.
Ningún campo está excluido de antemano: tú decides cuáles importan.

Las secciones del perfil:
- campos          : qué hay en cada campo y qué tan concentrado está
- cruces          : cuánto suma una medida por cada valor de otro campo
- coincidencias   : qué valor de un campo aparece junto a qué valor de otro
- actividad       : cuántos registros por mes
- textos          : lo que la gente escribió, literal

REGLAS ABSOLUTAS
1. NUNCA calcules. No sumes, no restes, no estimes porcentajes que no
   estén en el perfil. Si citas una cifra, cópiala tal cual.
2. NUNCA inventes causas. Lo que llame la atención se dice como
   observación, no como explicación. "Tres proveedores concentran el
   80% del volumen" sí; "por eso hay retrasos" no.
3. No nombres personas en términos negativos.
4. Español directo, sin adornos. Nada de "cabe destacar" ni "es
   importante mencionar".

QUÉ ES UN HALLAZGO
Una afirmación que conecta dos cosas o que señala algo fuera de lo
esperado, con su cifra. No es un hallazgo repetir un conteo.

  SÍ: "El 71% de los interesados en renovación viene de empresas que ya
       tienen licencia perpetua."
  SÍ: "La captura se concentró en marzo: 240 de los 364 registros."
  NO: "Se registraron 364 respuestas."
  NO: "El 98% llenó el teléfono."
  NO: "Hay varias empresas sin un predominio claro."

QUÉ NO DECIR NUNCA
- Tasas de completitud, salvo que un campo esté vacío en más de la
  mitad de los registros y eso comprometa el análisis.
- Mínimos, máximos o sumas de identificadores.
- Observaciones que no dicen nada. Si no hay patrón, omítelo.

Si tras revisar el perfil no hay nada que le cambie una decisión a un
gerente, devuelve un solo hallazgo diciéndolo con franqueza y explica en
"limitacion" qué tendría que capturar el formulario para que el análisis
sirviera. Es mejor eso que rellenar con obviedades.

FORMATO DE SALIDA
Un objeto JSON, nada más:

{
  "titular": "una frase de máximo 90 caracteres con lo más importante",
  "metricas": [
    {"etiqueta": "Registros", "valor": "364"},
    {"etiqueta": "Interés más frecuente", "valor": "Renovación de licencias"}
  ],
  "hallazgos": [
    "Afirmación concreta con su cifra.",
    "Otra."
  ],
  "destacado": {
    "titulo": "qué muestra el gráfico, en pocas palabras",
    "fuente": "campo" | "cruce",
    "campo": "nombre EXACTO del campo como aparece en el perfil",
    "medida": "nombre EXACTO de la medida, sólo si fuente es cruce"
  },
  "limitacion": "qué no se puede concluir con estos datos, o null"
}

Entre 2 y 4 métricas, entre 2 y 5 hallazgos.

Sobre "destacado": escoge el bloque del perfil que mejor sostenga tu
titular, para que se dibuje como barras. No inventes sus valores — el
servidor los adjunta. Si ninguno aporta, pon null.
Los valores de "metricas" se copian del perfil, nunca se calculan."""


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


def _resolver_destacado(analisis, perfil):
    """Adjunta las cifras reales al bloque que el modelo escogió graficar.

    El modelo sólo nombra qué quiere mostrar; los valores salen del
    perfil. Así una barra nunca puede traer un número inventado.
    """
    d = analisis.get('destacado')
    if not isinstance(d, dict):
        analisis.pop('destacado', None)
        return

    nombre = (d.get('campo') or '').strip().lower()
    medida = (d.get('medida') or '').strip().lower()

    if d.get('fuente') == 'cruce':
        for c in perfil.get('cruces', []):
            if (c['agrupado_por'].lower() == nombre
                    and (not medida or c['medida'].lower() == medida)):
                d['filas'] = [{'valor': f['valor'], 'cantidad': f['suma']}
                              for f in c['filas']]
                d['unidad'] = c['medida']
                return
    else:
        for c in perfil.get('campos', []):
            if c['nombre'].lower() == nombre:
                d['filas'] = [{'valor': v['valor'], 'cantidad': v['veces']}
                              for v in c['valores_frecuentes']]
                d['unidad'] = 'registros'
                return

    # El modelo nombró algo que no existe: mejor sin gráfico que con
    # uno que no corresponde.
    analisis.pop('destacado', None)


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
                SELECT rf.respuestas, rf.created_at
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
            registros = cursor.fetchall()

            if not registros:
                return jsonify({'sin_datos': True,
                                'texto': 'Este formulario no tiene respuestas '
                                         'en el periodo filtrado.'})

            filas = []
            for respuestas, creado in registros:
                filas.extend(_filas_planas(respuestas, campos, creado))

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
        _resolver_destacado(analisis, perfil)
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
