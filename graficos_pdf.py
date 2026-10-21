# -*- coding: utf-8 -*-
"""
graficos_pdf.py — Dibuja las visualizaciones de un tablero en un PDF.

Los gráficos del tablero los dibuja Chart.js en el navegador, y la
exportación actual captura ese canvas como PNG. Un informe programado
corre en un cron sin navegador, así que necesita dibujarlos del lado
del servidor.

Se hace con primitivas de FPDF —rectángulos, líneas y elipses— en vez
de con una librería de gráficos. Tres razones:

  Sin dependencias   fpdf2 ya está instalado. matplotlib traería unos
                     40 MB con numpy, y el servicio va justo de memoria.
  Vectorial          se imprime nítido a cualquier zoom, y el PDF pesa
                     una fracción de lo que pesaría con imágenes.
  Fiel a la marca    el color, los grosores y la tipografía se controlan
                     aquí, así que se parece más al tablero de lo que se
                     parecería la estética propia de otra librería.

Sólo se usan primitivas que existen en todas las versiones de fpdf2:
rect, line, ellipse, set_xy y cell. En particular la dona NO usa arcos
—que cambian entre versiones— sino un abanico de líneas finas desde el
centro, tapado luego por un círculo blanco. El resultado es idéntico y
no depende de una API que quizá no esté.
"""

import math

# Paleta para series múltiples. La primera entrada se reemplaza en
# tiempo de ejecución por el color de la empresa.
PALETA = [
    (251, 175, 51),   # marca (se sobrescribe)
    (59, 130, 246),
    (16, 185, 129),
    (239, 68, 68),
    (139, 92, 246),
    (236, 72, 153),
    (245, 158, 11),
    (20, 184, 166),
    (100, 116, 139),
]

GRIS_EJE    = (203, 207, 213)
GRIS_TEXTO  = (107, 114, 128)
NEGRO       = (31, 41, 55)
BLANCO      = (255, 255, 255)

# A partir de aquí las etiquetas no caben en vertical y el gráfico se
# dibuja en barras horizontales, que admiten textos largos.
MAX_BARRAS_VERTICALES = 6


# ══════════════════════════════════════════════════════════════════════
#  UTILIDADES
# ══════════════════════════════════════════════════════════════════════

def hex_a_rgb(h, por_defecto=(251, 175, 51)):
    try:
        h = (h or '').strip().lstrip('#')
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except (ValueError, IndexError):
        return por_defecto


def paleta_de(color_marca):
    p = list(PALETA)
    p[0] = color_marca
    return p


def _texto_plano(t):
    """Respaldo cuando el PDF usa fuentes core, que son latin-1."""
    return str(t if t is not None else '').encode('latin-1', 'replace').decode('latin-1')


def num_es(v, decimales=None):
    """Formato colombiano: 1.234,5"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return '0'
    if decimales is None:
        decimales = 0 if abs(v - round(v)) < 0.005 else 1
    s = f"{v:,.{decimales}f}"
    return s.replace(',', '§').replace('.', ',').replace('§', '.')


def _tope(maximo):
    """Techo 'redondo' del eje, para que las líneas guía caigan en
    números legibles en vez de en 8.374,2."""
    if maximo <= 0:
        return 1
    exp  = math.floor(math.log10(maximo))
    base = 10 ** exp
    for mult in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if maximo <= base * mult:
            return base * mult
    return base * 10


def _recortar(pdf, t, ancho):
    """Corta el texto con puntos suspensivos si no cabe."""
    t = str(t or '')
    if pdf.get_string_width(t) <= ancho:
        return t
    while t and pdf.get_string_width(t + '…') > ancho:
        t = t[:-1]
    return (t + '…') if t else ''


def _fuente(pdf, tam, negrita=False):
    try:
        pdf.set_font(pdf.font_family or 'Helvetica', 'B' if negrita else '', tam)
    except Exception:
        pdf.set_font('Helvetica', 'B' if negrita else '', tam)


# ══════════════════════════════════════════════════════════════════════
#  PIEZAS COMUNES
# ══════════════════════════════════════════════════════════════════════

def _titulo(pdf, x, y, w, texto, txt):
    if not texto:
        return y
    _fuente(pdf, 10, True)
    pdf.set_text_color(*NEGRO)
    pdf.set_xy(x, y)
    pdf.cell(w, 5, txt(_recortar(pdf, texto, w)), 0, 0, 'L')
    return y + 7


def _sin_datos(pdf, x, y, w, h, txt):
    _fuente(pdf, 9)
    pdf.set_text_color(*GRIS_TEXTO)
    pdf.set_xy(x, y + h / 2 - 3)
    pdf.cell(w, 6, txt('Sin datos en el periodo'), 0, 0, 'C')


def _rejilla(pdf, x, y, w, h, tope, pasos=4):
    """Líneas guía horizontales con su valor a la izquierda."""
    _fuente(pdf, 6.5)
    pdf.set_text_color(*GRIS_TEXTO)
    pdf.set_draw_color(*GRIS_EJE)
    pdf.set_line_width(0.1)
    for i in range(pasos + 1):
        vy = y + h - (h * i / pasos)
        pdf.line(x, vy, x + w, vy)
        pdf.set_xy(x - 17, vy - 2)
        pdf.cell(15, 4, txt_num(tope * i / pasos), 0, 0, 'R')


def txt_num(v):
    return num_es(v)


# ══════════════════════════════════════════════════════════════════════
#  BARRAS
# ══════════════════════════════════════════════════════════════════════

def barras_verticales(pdf, x, y, w, h, labels, valores, color, txt):
    tope = _tope(max(valores))
    _rejilla(pdf, x, y, w, h, tope)

    n     = len(labels)
    paso  = w / n
    ancho = min(paso * 0.6, 22)

    pdf.set_fill_color(*color)
    for i, v in enumerate(valores):
        alto = (v / tope) * h if tope else 0
        bx   = x + paso * i + (paso - ancho) / 2
        if alto > 0.3:
            pdf.rect(bx, y + h - alto, ancho, alto, 'F')

        _fuente(pdf, 6.5, True)
        pdf.set_text_color(*NEGRO)
        pdf.set_xy(bx - 4, y + h - alto - 4.5)
        pdf.cell(ancho + 8, 4, txt(num_es(v)), 0, 0, 'C')

        _fuente(pdf, 6.5)
        pdf.set_text_color(*GRIS_TEXTO)
        pdf.set_xy(x + paso * i, y + h + 1.5)
        pdf.cell(paso, 4, txt(_recortar(pdf, labels[i], paso - 1)), 0, 0, 'C')


def barras_horizontales(pdf, x, y, w, h, labels, valores, color, txt):
    """Para muchas categorías o etiquetas largas: el texto cabe."""
    n = len(labels)
    alto_fila = min(h / n, 11)
    alto_barra = min(alto_fila * 0.62, 7)

    _fuente(pdf, 7)
    ancho_label = min(max((pdf.get_string_width(str(l)) for l in labels),
                          default=20) + 2, w * 0.38)
    ancho_valor = 18
    ancho_pista = w - ancho_label - ancho_valor - 4
    tope = _tope(max(valores))

    for i, v in enumerate(valores):
        fy = y + alto_fila * i + (alto_fila - alto_barra) / 2

        _fuente(pdf, 7)
        pdf.set_text_color(*GRIS_TEXTO)
        pdf.set_xy(x, fy - 0.5)
        pdf.cell(ancho_label, alto_barra + 1,
                 txt(_recortar(pdf, labels[i], ancho_label - 1)), 0, 0, 'L')

        bx = x + ancho_label + 2
        pdf.set_fill_color(241, 242, 244)
        pdf.rect(bx, fy, ancho_pista, alto_barra, 'F')

        largo = (v / tope) * ancho_pista if tope else 0
        if largo > 0.3:
            pdf.set_fill_color(*color)
            pdf.rect(bx, fy, largo, alto_barra, 'F')

        _fuente(pdf, 7, True)
        pdf.set_text_color(*NEGRO)
        pdf.set_xy(bx + ancho_pista + 2, fy - 0.5)
        pdf.cell(ancho_valor, alto_barra + 1, txt(num_es(v)), 0, 0, 'R')


def barras_agrupadas(pdf, x, y, w, h, labels, series, paleta, txt):
    todos = [v for s in series for v in s.get('valores', [])] or [0]
    tope  = _tope(max(todos))
    _rejilla(pdf, x, y, w, h, tope)

    n_cat  = len(labels)
    n_ser  = len(series)
    paso   = w / n_cat
    ancho  = min((paso * 0.72) / n_ser, 9)

    for si, s in enumerate(series):
        pdf.set_fill_color(*paleta[si % len(paleta)])
        for i, v in enumerate(s.get('valores', [])):
            alto = (v / tope) * h if tope else 0
            bx = (x + paso * i + (paso - ancho * n_ser) / 2 + ancho * si)
            if alto > 0.3:
                pdf.rect(bx, y + h - alto, ancho, alto, 'F')

    _fuente(pdf, 6.5)
    pdf.set_text_color(*GRIS_TEXTO)
    for i, l in enumerate(labels):
        pdf.set_xy(x + paso * i, y + h + 1.5)
        pdf.cell(paso, 4, txt(_recortar(pdf, l, paso - 1)), 0, 0, 'C')

    leyenda(pdf, x, y + h + 7, w, [s.get('nombre', '') for s in series],
            paleta, txt)


def leyenda(pdf, x, y, w, nombres, paleta, txt):
    _fuente(pdf, 6.5)
    cx = x
    for i, nombre in enumerate(nombres):
        pdf.set_fill_color(*paleta[i % len(paleta)])
        pdf.rect(cx, y + 1, 3, 3, 'F')
        pdf.set_text_color(*GRIS_TEXTO)
        ancho = pdf.get_string_width(str(nombre)) + 7
        pdf.set_xy(cx + 4.5, y)
        pdf.cell(ancho, 5, txt(nombre), 0, 0, 'L')
        cx += ancho + 4
        if cx > x + w - 20:
            break


# ══════════════════════════════════════════════════════════════════════
#  LÍNEA
# ══════════════════════════════════════════════════════════════════════

def linea(pdf, x, y, w, h, labels, valores, color, txt):
    tope = _tope(max(valores))
    _rejilla(pdf, x, y, w, h, tope)

    n = len(valores)
    paso = w / max(n - 1, 1)
    puntos = [(x + paso * i, y + h - (v / tope) * h if tope else y + h)
              for i, v in enumerate(valores)]

    pdf.set_draw_color(*color)
    pdf.set_line_width(0.6)
    for i in range(len(puntos) - 1):
        pdf.line(puntos[i][0], puntos[i][1], puntos[i + 1][0], puntos[i + 1][1])

    # Un punto por vértice: con pocos datos la línea sola no se lee.
    pdf.set_fill_color(*color)
    if n <= 30:
        for px, py in puntos:
            pdf.ellipse(px - 0.9, py - 0.9, 1.8, 1.8, 'F')

    # Etiquetas: sólo las que caben, repartidas de punta a punta.
    _fuente(pdf, 6.5)
    pdf.set_text_color(*GRIS_TEXTO)
    max_etiq = max(int(w / 20), 2)
    salto    = max(1, math.ceil(n / max_etiq))
    for i in range(0, n, salto):
        pdf.set_xy(puntos[i][0] - 10, y + h + 1.5)
        pdf.cell(20, 4, txt(_recortar(pdf, labels[i], 19)), 0, 0, 'C')


# ══════════════════════════════════════════════════════════════════════
#  DONA
# ══════════════════════════════════════════════════════════════════════

def dona(pdf, x, y, w, h, labels, valores, paleta, txt, etiqueta_centro='TOTAL'):
    """Sectores rellenos con un abanico de líneas desde el centro.

    Se evita a propósito cualquier función de arco: no todas las
    versiones de fpdf2 la traen con la misma firma, y una línea fina
    repetida da el mismo resultado con primitivas garantizadas.
    """
    total = sum(valores)
    if total <= 0:
        _sin_datos(pdf, x, y, w, h, txt)
        return

    lado = min(w * 0.55, h)
    r    = lado / 2
    cx   = x + r + 2
    cy   = y + h / 2

    pdf.set_line_width(0.25)
    angulo = -math.pi / 2          # arranca arriba
    for i, v in enumerate(valores):
        barrido = 2 * math.pi * (v / total)
        if barrido <= 0:
            continue
        pdf.set_draw_color(*paleta[i % len(paleta)])
        pasos = max(int(barrido * r * 3), 3)
        for k in range(pasos + 1):
            a = angulo + barrido * k / pasos
            pdf.line(cx, cy, cx + r * math.cos(a), cy + r * math.sin(a))
        angulo += barrido

    # El agujero
    pdf.set_fill_color(*BLANCO)
    pdf.ellipse(cx - r * 0.58, cy - r * 0.58, r * 1.16, r * 1.16, 'F')

    _fuente(pdf, 11, True)
    pdf.set_text_color(*NEGRO)
    pdf.set_xy(cx - r, cy - 4)
    pdf.cell(r * 2, 5, txt(num_es(total)), 0, 0, 'C')
    _fuente(pdf, 5.5)
    pdf.set_text_color(*GRIS_TEXTO)
    pdf.set_xy(cx - r, cy + 1)
    pdf.cell(r * 2, 4, txt(etiqueta_centro), 0, 0, 'C')

    # Leyenda a la derecha, con su valor
    lx = cx + r + 6
    lw = x + w - lx
    _fuente(pdf, 7)
    paso = min(6, h / max(len(labels), 1))
    for i, l in enumerate(labels[:8]):
        ly = y + (h - paso * min(len(labels), 8)) / 2 + paso * i
        pdf.set_fill_color(*paleta[i % len(paleta)])
        pdf.rect(lx, ly + 1.4, 3, 3, 'F')
        pdf.set_text_color(*GRIS_TEXTO)
        pdf.set_xy(lx + 4.5, ly)
        pdf.cell(lw - 22, paso, txt(_recortar(pdf, l, lw - 24)), 0, 0, 'L')
        pdf.set_text_color(*NEGRO)
        pdf.set_xy(lx + lw - 20, ly)
        pdf.cell(20, paso, txt(num_es(valores[i])), 0, 0, 'R')


# ══════════════════════════════════════════════════════════════════════
#  PARETO
# ══════════════════════════════════════════════════════════════════════

def pareto(pdf, x, y, w, h, labels, valores, color, txt):
    """Barras por impacto más la curva acumulada y la línea del 80%."""
    pares = sorted(zip(labels, valores), key=lambda p: -p[1])
    labels  = [p[0] for p in pares]
    valores = [p[1] for p in pares]

    total = sum(valores) or 1
    tope  = _tope(max(valores))
    _rejilla(pdf, x, y, w, h, tope)

    n     = len(labels)
    paso  = w / n
    ancho = min(paso * 0.6, 20)

    pdf.set_fill_color(*color)
    for i, v in enumerate(valores):
        alto = (v / tope) * h
        bx   = x + paso * i + (paso - ancho) / 2
        if alto > 0.3:
            pdf.rect(bx, y + h - alto, ancho, alto, 'F')

    # Curva acumulada sobre un eje porcentual propio
    acum, puntos = 0, []
    for i, v in enumerate(valores):
        acum += v
        puntos.append((x + paso * i + paso / 2, y + h - (acum / total) * h))

    pdf.set_draw_color(*GRIS_TEXTO)
    pdf.set_line_width(0.5)
    for i in range(len(puntos) - 1):
        pdf.line(*puntos[i], *puntos[i + 1])
    pdf.set_fill_color(*GRIS_TEXTO)
    for px, py in puntos:
        pdf.ellipse(px - 0.8, py - 0.8, 1.6, 1.6, 'F')

    # El 80%, que es de lo que trata el principio
    y80 = y + h - h * 0.8
    pdf.set_draw_color(220, 38, 38)
    pdf.set_line_width(0.2)
    paso_guion = 2.5
    gx = x
    while gx < x + w:
        pdf.line(gx, y80, min(gx + 1.4, x + w), y80)
        gx += paso_guion
    _fuente(pdf, 6)
    pdf.set_text_color(220, 38, 38)
    pdf.set_xy(x + w - 14, y80 - 3.5)
    pdf.cell(14, 3.5, txt('80%'), 0, 0, 'R')

    _fuente(pdf, 6.5)
    pdf.set_text_color(*GRIS_TEXTO)
    for i, l in enumerate(labels):
        pdf.set_xy(x + paso * i, y + h + 1.5)
        pdf.cell(paso, 4, txt(_recortar(pdf, l, paso - 1)), 0, 0, 'C')


# ══════════════════════════════════════════════════════════════════════
#  TARJETAS
# ══════════════════════════════════════════════════════════════════════

def tarjetas(pdf, x, y, w, h, labels, valores, color, txt):
    n    = min(len(labels), 8)
    cols = 4 if n > 3 else max(n, 1)
    filas = math.ceil(n / cols)
    cw = (w - 4 * (cols - 1)) / cols
    ch = min((h - 4 * (filas - 1)) / filas, 24)

    for i in range(n):
        cx = x + (cw + 4) * (i % cols)
        cy = y + (ch + 4) * (i // cols)

        pdf.set_fill_color(250, 250, 251)
        pdf.set_draw_color(*GRIS_EJE)
        pdf.set_line_width(0.1)
        pdf.rect(cx, cy, cw, ch, 'DF')

        _fuente(pdf, 6)
        pdf.set_text_color(*GRIS_TEXTO)
        pdf.set_xy(cx + 2, cy + 2.5)
        pdf.cell(cw - 4, 3.5, txt(_recortar(pdf, labels[i], cw - 4)), 0, 0, 'L')

        _fuente(pdf, 13, True)
        pdf.set_text_color(*color)
        pdf.set_xy(cx + 2, cy + 7)
        pdf.cell(cw - 4, 8, txt(num_es(valores[i])), 0, 0, 'L')


# ══════════════════════════════════════════════════════════════════════
#  DESPACHADOR
# ══════════════════════════════════════════════════════════════════════

def dibujar(pdf, tipo, datos, x, y, w, h, color_marca=(251, 175, 51),
            titulo=None, txt=None):
    """Dibuja una visualización dentro del rectángulo indicado.

    `datos` es lo que devuelve calcular_datos_viz(): {labels, valores}
    o {labels, series} para barras agrupadas.

    `txt` adapta el texto a la fuente del PDF; si no se pasa, se usa un
    saneo latin-1 que sirve con las fuentes core de FPDF.
    """
    txt = txt or _texto_plano
    paleta = paleta_de(color_marca)

    # Espacio reservado: título arriba, etiquetas del eje abajo, y un
    # margen izquierdo para los valores de la rejilla.
    y_grafico = _titulo(pdf, x, y, w, titulo, txt)
    alto_util = h - (y_grafico - y) - 7
    x_grafico = x + 18
    ancho_util = w - 18

    if alto_util < 12:
        return

    labels  = (datos or {}).get('labels') or []
    valores = (datos or {}).get('valores') or []
    series  = (datos or {}).get('series') or []

    if not labels or (not valores and not series):
        _sin_datos(pdf, x, y_grafico, w, alto_util, txt)
        return

    try:
        if tipo == 'dona':
            dona(pdf, x, y_grafico, w, alto_util, labels, valores, paleta, txt)

        elif tipo == 'linea':
            linea(pdf, x_grafico, y_grafico, ancho_util, alto_util,
                  labels, valores, paleta[0], txt)

        elif tipo == 'pareto':
            pareto(pdf, x_grafico, y_grafico, ancho_util, alto_util,
                   labels, valores, paleta[0], txt)

        elif tipo == 'tarjetas':
            tarjetas(pdf, x, y_grafico, w, alto_util, labels, valores,
                     paleta[0], txt)

        elif series:
            barras_agrupadas(pdf, x_grafico, y_grafico, ancho_util,
                             alto_util - 7, labels, series, paleta, txt)

        elif len(labels) <= MAX_BARRAS_VERTICALES:
            barras_verticales(pdf, x_grafico, y_grafico, ancho_util,
                              alto_util, labels, valores, paleta[0], txt)

        else:
            # Muchas categorías: en vertical las etiquetas se pisan.
            barras_horizontales(pdf, x, y_grafico, w, alto_util,
                                labels, valores, paleta[0], txt)

    except Exception:
        # Un gráfico que falle no debe tumbar el informe completo.
        _sin_datos(pdf, x, y_grafico, w, alto_util, txt)
    finally:
        pdf.set_line_width(0.2)
        pdf.set_text_color(*NEGRO)
