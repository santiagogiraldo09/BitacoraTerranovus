# -*- coding: utf-8 -*-
"""
generador_informe.py — Arma el PDF de un informe programado.

Un informe tiene dos partes de origen distinto:

  La plantilla   portada, encabezado y pie. Es la papelería de la
                 empresa y no cambia entre informes.
  Los tableros   el contenido. Se resuelven al generar, así que un
                 tablero al que le agregaron un gráfico aparece con él
                 sin reconfigurar nada.

La portada ocupa sola la primera hoja. Desde la segunda se repiten el
encabezado y el pie, y entre ellos va el contenido de los tableros.

Los gráficos los dibuja `graficos_pdf` con primitivas de FPDF, no el
navegador: este módulo tiene que poder correr en un cron a las siete de
la mañana, sin nadie en pantalla y sin Chart.js.
"""

import io
import math
import unicodedata

from fpdf import FPDF

import graficos_pdf as G

# ── Geometría de la hoja, en milímetros ──────────────────────────────
MARGEN        = 15
ANCHO_UTIL    = 210 - MARGEN * 2
ALTO_PAGINA   = 297
ALTO_FRANJA   = 15          # encabezado y pie
Y_CONTENIDO   = MARGEN + ALTO_FRANJA + 4
Y_LIMITE      = ALTO_PAGINA - MARGEN - ALTO_FRANJA - 4

ALTO_VIZ      = 64          # caja de un gráfico a ancho completo
ALTO_VIZ_MED  = 58          # caja de un gráfico a media página
SEPARACION    = 8

PT = {'titulo_grande': 24, 'titulo': 17, 'subtitulo': 13,
      'normal': 10, 'pequeno': 8}
ESPACIO_MM = {'compacto': 1.5, 'normal': 5, 'amplio': 12}
ALINEA = {'izquierda': 'L', 'centro': 'C', 'derecha': 'R'}
RANURA_ALINEA = {'izquierda': 'L', 'centro': 'C', 'derecha': 'R'}


def limpiar(t):
    """Texto apto para las fuentes core de FPDF, que son latin-1.

    Se quitan las tildes en vez de reemplazarlas por '?': 'Operación'
    sale 'Operacion', que es legible, y no 'Operaci?n'.
    """
    t = str(t if t is not None else '')
    t = unicodedata.normalize('NFKD', t)
    t = ''.join(c for c in t if not unicodedata.combining(c))
    return t.encode('latin-1', 'replace').decode('latin-1')


def _color(clave, marca):
    return {'marca': marca, 'gris': G.GRIS_TEXTO}.get(clave, G.NEGRO)


def _estilo_fuente(e):
    s = ''
    if e.get('negrita'): s += 'B'
    if e.get('cursiva'): s += 'I'
    return s


# ══════════════════════════════════════════════════════════════════════
#  EL DOCUMENTO
# ══════════════════════════════════════════════════════════════════════

class InformePDF(FPDF):
    """FPDF con el encabezado y el pie de la plantilla.

    FPDF llama a header() y footer() en cada página. Aquí se saltan en
    la primera, que es la portada: una carátula con encabezado repetido
    se ve como un error de maquetación.
    """

    def __init__(self, plantilla, contexto, marca, logo_bytes):
        super().__init__('P', 'mm', 'A4')
        self.plantilla  = plantilla or {}
        self.contexto   = contexto or {}
        self.marca      = marca
        self.logo_bytes = logo_bytes
        self.hay_portada = bool((self.plantilla.get('portada') or []))
        self.set_auto_page_break(False)
        self.set_margins(MARGEN, MARGEN, MARGEN)
        self.alias_nb_pages()          # alimenta {nb} -> total de páginas

    # ── Variables ──
    def resolver(self, texto):
        t = str(texto or '')
        for clave, valor in self.contexto.items():
            t = t.replace('{' + clave + '}', str(valor))
        t = t.replace('{pagina}', str(self.page_no()))
        t = t.replace('{paginas}', '{nb}')
        return t

    # ── Franjas ──
    def _franja(self, zona, y):
        bloques = (self.plantilla.get(zona) or {})
        ancho = ANCHO_UTIL / 3
        for i, ranura in enumerate(('izquierda', 'centro', 'derecha')):
            b = bloques.get(ranura)
            if not b:
                continue
            x = MARGEN + ancho * i
            self._bloque_en_caja(b, x, y, ancho, RANURA_ALINEA[ranura])

    def _bloque_en_caja(self, b, x, y, w, alineacion=None):
        tipo   = b.get('tipo')
        estilo = b.get('estilo') or {}
        ali    = alineacion or ALINEA.get(estilo.get('alineacion'), 'L')

        if tipo == 'logo':
            if not self.logo_bytes:
                return
            try:
                alto = 9
                img = io.BytesIO(self.logo_bytes)
                # x según la alineación: FPDF no centra imágenes solo.
                ancho_img = 26
                px = (x if ali == 'L'
                      else x + (w - ancho_img) / 2 if ali == 'C'
                      else x + w - ancho_img)
                self.image(img, px, y, h=alto)
            except Exception:
                pass
            return

        if tipo == 'separador':
            self.set_draw_color(*G.GRIS_EJE)
            self.set_line_width(0.3)
            self.line(x, y + 2, x + w, y + 2)
            return

        if tipo == 'imagen':
            url = (b.get('config') or {}).get('url')
            if not url:
                return
            try:
                datos = _descargar(url)
                if datos:
                    self.image(io.BytesIO(datos), x, y, w=min(w, 80))
            except Exception:
                pass
            return

        # titulo y texto
        self.set_font('Helvetica', _estilo_fuente(estilo),
                      PT.get(estilo.get('tamano'), 10))
        self.set_text_color(*_color(estilo.get('color'), self.marca))
        alto_linea = PT.get(estilo.get('tamano'), 10) * 0.42 + 1.5
        self.set_xy(x, y)
        self.multi_cell(w, alto_linea, limpiar(self.resolver(b.get('texto'))),
                        0, ali)

    def header(self):
        if self.hay_portada and self.page_no() == 1:
            return
        self._franja('encabezado', MARGEN)
        self.set_draw_color(*G.GRIS_EJE)
        self.set_line_width(0.1)
        self.line(MARGEN, MARGEN + ALTO_FRANJA - 2,
                  210 - MARGEN, MARGEN + ALTO_FRANJA - 2)

    def footer(self):
        if self.hay_portada and self.page_no() == 1:
            return
        y = ALTO_PAGINA - MARGEN - ALTO_FRANJA + 4
        self.set_draw_color(*G.GRIS_EJE)
        self.set_line_width(0.1)
        self.line(MARGEN, y - 3, 210 - MARGEN, y - 3)
        self._franja('pie', y)


def _descargar(url, limite=3_000_000):
    """Trae una imagen por HTTPS. Falla en silencio: una imagen que no
    carga no debe tumbar el informe."""
    try:
        import requests
        r = requests.get(url, timeout=8)
        if r.status_code == 200 and len(r.content) <= limite:
            return r.content
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════
#  PIEZAS DEL CONTENIDO
# ══════════════════════════════════════════════════════════════════════

def _portada(pdf):
    bloques = pdf.plantilla.get('portada') or []
    if not bloques:
        return
    y = 70          # deja aire arriba: una carátula no arranca en el borde
    for b in bloques:
        y += ESPACIO_MM.get((b.get('estilo') or {}).get('espacio'), 5)
        antes = pdf.get_y()
        pdf._bloque_en_caja(b, MARGEN, y, ANCHO_UTIL)
        # multi_cell deja el cursor al final; los demás no mueven y.
        y = max(pdf.get_y(), y + 8) if b.get('tipo') in ('titulo', 'texto') \
            else y + (12 if b.get('tipo') in ('logo', 'imagen') else 3)
        if y > 250:
            break


def _titulo_tablero(pdf, nombre, y):
    pdf.set_font('Helvetica', 'B', 13)
    pdf.set_text_color(*G.NEGRO)
    pdf.set_xy(MARGEN, y)
    pdf.cell(ANCHO_UTIL, 7, limpiar(nombre), 0, 0, 'L')
    pdf.set_draw_color(*pdf.marca)
    pdf.set_line_width(0.8)
    pdf.line(MARGEN, y + 8, MARGEN + 28, y + 8)
    return y + 13


def _tabla(pdf, datos, x, y, w, h, titulo):
    """Las tablas no las dibuja graficos_pdf: aquí se arma una compacta
    que respeta el alto disponible recortando filas."""
    columnas = (datos or {}).get('columnas') or []
    filas    = (datos or {}).get('filas') or []

    if titulo:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_text_color(*G.NEGRO)
        pdf.set_xy(x, y)
        pdf.cell(w, 5, limpiar(titulo), 0, 0, 'L')
        y += 7
        h -= 7

    if not columnas or not filas:
        pdf.set_font('Helvetica', '', 9)
        pdf.set_text_color(*G.GRIS_TEXTO)
        pdf.set_xy(x, y + h / 2 - 3)
        pdf.cell(w, 6, limpiar('Sin datos en el periodo'), 0, 0, 'C')
        return

    columnas = columnas[:6]
    cw = w / len(columnas)
    alto_fila = 5.4
    max_filas = max(int((h - alto_fila) / alto_fila), 1)

    pdf.set_fill_color(*pdf.marca)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 7)
    for i, c in enumerate(columnas):
        pdf.set_xy(x + cw * i, y)
        pdf.cell(cw, alto_fila, limpiar(c.get('nombre', ''))[:22], 0, 0, 'L', True)

    pdf.set_font('Helvetica', '', 7)
    for j, fila in enumerate(filas[:max_filas]):
        fy = y + alto_fila * (j + 1)
        if j % 2 == 0:
            pdf.set_fill_color(250, 250, 251)
            pdf.rect(x, fy, w, alto_fila, 'F')
        pdf.set_text_color(*G.NEGRO)
        for i, c in enumerate(columnas):
            pdf.set_xy(x + cw * i, fy)
            valor = str(fila.get(c.get('campo_id'), '—'))
            pdf.cell(cw, alto_fila, limpiar(valor)[:26], 0, 0, 'L')

    if len(filas) > max_filas:
        pdf.set_font('Helvetica', 'I', 6.5)
        pdf.set_text_color(*G.GRIS_TEXTO)
        pdf.set_xy(x, y + alto_fila * (max_filas + 1) + 0.5)
        pdf.cell(w, 4, limpiar(f'… y {len(filas) - max_filas} filas más'),
                 0, 0, 'R')


# ══════════════════════════════════════════════════════════════════════
#  ENTRADA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════

def generar(cursor, empresa_id, config, contexto_extra=None):
    """Devuelve el PDF del informe en bytes.

    config: {proyecto_id, plantilla_id, tableros, ventana,
             ventana_dias, zona_horaria, nombre}
    """
    from app import calcular_datos_viz, _ventana_reporte

    proyecto_id = config.get('proyecto_id')
    tableros_id = config.get('tableros') or []

    # ── Marca y proyecto ──
    cursor.execute("""
        SELECT p.nombre_proyecto, e.nombre, e.logo_url,
               COALESCE(NULLIF(e.color_primario, ''), '#FBAF33')
        FROM proyectos p JOIN empresas e ON e.id = p.empresa_id
        WHERE p.id = %s AND p.empresa_id = %s
    """, (proyecto_id, empresa_id))
    fila = cursor.fetchone()
    if not fila:
        raise ValueError('Proyecto no válido')
    proyecto_nombre, empresa_nombre, logo_url, color_hex = fila
    marca = G.hex_a_rgb(color_hex)

    # ── Plantilla ──
    plantilla = {}
    if config.get('plantilla_id'):
        cursor.execute("""
            SELECT secciones FROM plantillas_informe
            WHERE id = %s AND empresa_id = %s
        """, (config['plantilla_id'], empresa_id))
        f = cursor.fetchone()
        if f:
            from informes import _limpiar_plantilla
            plantilla = _limpiar_plantilla(f[0])

    # ── Periodo ──
    desde, hasta, _clave, etiqueta = _ventana_reporte(
        config.get('ventana'), config.get('ventana_dias'),
        config.get('zona_horaria', 'America/Bogota'))

    filtros = {'proyecto_id': proyecto_id,
               'desde': desde.strftime('%Y-%m-%d'),
               # 'hasta' es exclusivo en la ventana; el filtro de los
               # gráficos compara con <=, así que se resta un día.
               'hasta': (hasta.date() - __import__('datetime').timedelta(days=1)
                         ).strftime('%Y-%m-%d')}

    contexto = {
        'proyecto':  proyecto_nombre,
        'empresa':   empresa_nombre,
        'periodo':   etiqueta,
        'desde':     desde.strftime('%d/%m/%Y'),
        'hasta':     (hasta.date() - __import__('datetime').timedelta(days=1)
                      ).strftime('%d/%m/%Y'),
        'fecha':     __import__('datetime').datetime.now().strftime('%d/%m/%Y'),
        'usuario':   '',
        'registros': '',
    }
    contexto.update(contexto_extra or {})

    logo_bytes = _descargar(logo_url) if logo_url else None
    pdf = InformePDF(plantilla, contexto, marca, logo_bytes)

    # ── Hoja 1: portada ──
    if plantilla.get('portada'):
        pdf.add_page()
        _portada(pdf)

    # ── Hoja 2 en adelante: los tableros ──
    pdf.add_page()
    y = Y_CONTENIDO

    for tid in tableros_id:
        cursor.execute("""
            SELECT nombre FROM tableros_bi
            WHERE id = %s AND empresa_id = %s
        """, (tid, empresa_id))
        t = cursor.fetchone()
        if not t:
            continue

        if y + 30 > Y_LIMITE:
            pdf.add_page(); y = Y_CONTENIDO
        y = _titulo_tablero(pdf, t[0], y)

        cursor.execute("""
            SELECT tipo, titulo, config FROM visualizaciones_bi
            WHERE tablero_id = %s ORDER BY posicion
        """, (tid,))
        vizs = cursor.fetchall()

        if not vizs:
            pdf.set_font('Helvetica', 'I', 9)
            pdf.set_text_color(*G.GRIS_TEXTO)
            pdf.set_xy(MARGEN, y)
            pdf.cell(ANCHO_UTIL, 6, limpiar('Este tablero no tiene gráficos.'),
                     0, 0, 'L')
            y += 12
            continue

        columna = 0          # 0 = izquierda, 1 = derecha
        for tipo, titulo, cfg in vizs:
            cfg = cfg or {}
            completo = cfg.get('ancho_completo', tipo == 'tabla')
            alto = ALTO_VIZ if completo else ALTO_VIZ_MED

            if completo and columna == 1:
                y += ALTO_VIZ_MED + SEPARACION
                columna = 0

            if y + alto > Y_LIMITE:
                pdf.add_page(); y = Y_CONTENIDO; columna = 0

            datos = calcular_datos_viz(cfg.get('datasets', []), filtros,
                                       empresa_id, cursor_ext=cursor)
            if 'error' in datos:
                datos = {}

            if completo:
                x, w = MARGEN, ANCHO_UTIL
            else:
                w = (ANCHO_UTIL - SEPARACION) / 2
                x = MARGEN + (w + SEPARACION) * columna

            if tipo == 'tabla':
                _tabla(pdf, datos, x, y, w, alto, titulo)
            else:
                G.dibujar(pdf, tipo, datos, x, y, w, alto,
                          color_marca=marca, titulo=titulo, txt=limpiar)

            if completo:
                y += alto + SEPARACION
            else:
                columna += 1
                if columna == 2:
                    columna = 0
                    y += alto + SEPARACION

        if columna == 1:            # cerrar una fila a medias
            y += ALTO_VIZ_MED + SEPARACION

    salida = pdf.output(dest='S')
    return bytes(salida.encode('latin-1')) if isinstance(salida, str) else bytes(salida)
