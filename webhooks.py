"""
webhooks.py — Avisos salientes de Bitácora Digital.

Cuando ocurre un hecho (se guarda un registro, se crea un proyecto),
Bitácora hace un POST firmado a las URLs que el administrador haya
registrado. El envío va en segundo plano: un servidor lento del cliente
no puede dejar esperando a quien está guardando en obra.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

# ──────────────────────────── Constantes ────────────────────────────

EVENTOS_VALIDOS = {
    'registro.creado',
    'registro.actualizado',
    'proyecto.creado',
}

TIMEOUT = 10                    # el receptor debe acusar rápido
ESPERAS = (0, 30, 120)          # tres intentos: ya, 30 s y 2 min
FALLOS_PARA_DESACTIVAR = 10
MAX_ENVIOS_SIMULTANEOS = 4

_semaforo = threading.Semaphore(MAX_ENVIOS_SIMULTANEOS)


# ──────────────────────────── Secretos ────────────────────────────

_FERNET = None


def _fernet():
    """Cifrador para el secreto de firma.

    A diferencia de las claves de API, el secreto NO se puede guardar
    hasheado: hace falta en claro para firmar cada mensaje. Se cifra en
    reposo con la misma llave de entorno que ya usa Synchro.
    """
    global _FERNET
    if _FERNET is None:
        from cryptography.fernet import Fernet
        clave = (os.environ.get('WEBHOOK_FERNET_KEY')
                 or os.environ.get('SYNCHRO_FERNET_KEY'))
        if not clave:
            raise RuntimeError(
                'Falta WEBHOOK_FERNET_KEY (o SYNCHRO_FERNET_KEY) en el entorno')
        _FERNET = Fernet(clave)
    return _FERNET


def generar_secreto():
    """Secreto nuevo. Se le muestra al administrador una sola vez."""
    return 'whsec_' + secrets.token_urlsafe(32)


def cifrar_secreto(secreto):
    return _fernet().encrypt(secreto.encode('utf-8')).decode('ascii')


def descifrar_secreto(cifrado):
    return _fernet().decrypt(cifrado.encode('ascii')).decode('utf-8')


# ──────────────────────── Validación de la URL ────────────────────────

def _destino_es_interno(host):
    """True si el dominio resuelve a una dirección de la red interna.

    Sin esto, alguien podría registrar como webhook una dirección interna
    y usar a Bitácora para alcanzar servicios que desde fuera no son
    accesibles. Es el agujero clásico de cualquier implementación de
    webhooks.
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f'No se pudo resolver el dominio «{host}».')

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def validar_url(url):
    """Devuelve la URL si es un destino admisible; si no, levanta ValueError."""
    url = (url or '').strip()
    partes = urlparse(url)

    if partes.scheme != 'https':
        raise ValueError('La URL debe empezar con https://')
    if not partes.hostname:
        raise ValueError('La URL no tiene un dominio válido.')
    if _destino_es_interno(partes.hostname):
        raise ValueError('La URL apunta a una dirección de red interna.')

    return url


# ──────────────────────────── Firma ────────────────────────────

def firmar(secreto, marca_tiempo, cuerpo_bytes):
    """HMAC-SHA256 sobre «marca_tiempo.cuerpo».

    Se incluye la marca de tiempo para que el receptor pueda rechazar un
    mensaje viejo que alguien haya capturado y esté reenviando. Firmar
    sólo el cuerpo dejaría esa puerta abierta.
    """
    mensaje = f'{marca_tiempo}.'.encode('utf-8') + cuerpo_bytes
    firma = hmac.new(secreto.encode('utf-8'), mensaje, hashlib.sha256)
    return 'sha256=' + firma.hexdigest()


# ──────────────────────────── Mensaje ────────────────────────────

def _armar_mensaje(cursor, evento, empresa_id, ctx):
    """Construye el JSON del aviso, con identificadores y nombres.

    Los números sirven para que la máquina los use; los nombres para que
    una persona entienda el aviso sin consultar nada más.
    """
    ahora = datetime.now(timezone.utc).isoformat()
    base = {
        'evento':      evento,
        'id_evento':   'ev_' + uuid.uuid4().hex,
        'ocurrido_en': ahora,
    }

    if evento == 'proyecto.creado':
        cursor.execute("""
            SELECT p.id, p.nombre_proyecto, u.name, u.apellido
            FROM proyectos p
            LEFT JOIN usuario u ON u.user_id = %s
            WHERE p.id = %s AND p.empresa_id = %s
        """, (ctx.get('user_id'), ctx.get('proyecto_id'), empresa_id))
        fila = cursor.fetchone()
        if not fila:
            return None

        autor = (f'{fila[2] or ""} {fila[3] or ""}').strip() or None
        base['resumen'] = f'Se creó el proyecto «{fila[1]}»'
        base['datos'] = {
            'proyecto': {'id': fila[0], 'nombre': fila[1]},
            'autor':    {'id': ctx.get('user_id'), 'nombre': autor},
        }
        return base

    # registro.creado / registro.actualizado
    cursor.execute("""
        SELECT rf.id, rf.created_at,
               p.id, p.nombre_proyecto,
               f.id, f.nombre,
               u.user_id, u.name, u.apellido
        FROM respuestas_formulario rf
        JOIN proyectos p    ON p.id = rf.id_proyecto
        LEFT JOIN formularios f ON f.id = rf.formulario_id
        LEFT JOIN usuario u     ON u.user_id = rf.user_id
        WHERE rf.id = %s AND p.empresa_id = %s
    """, (ctx.get('registro_id'), empresa_id))
    fila = cursor.fetchone()
    if not fila:
        return None

    autor = (f'{fila[7] or ""} {fila[8] or ""}').strip() or None
    verbo = 'creó' if evento == 'registro.creado' else 'actualizó'
    nombre_form = fila[5] or 'formulario'

    base['resumen'] = (f'Se {verbo} un registro de «{nombre_form}» '
                       f'en el proyecto «{fila[3]}»')
    base['datos'] = {
        'registro':   {'id': fila[0],
                       'creado_en': fila[1].isoformat() if fila[1] else None},
        'proyecto':   {'id': fila[2], 'nombre': fila[3]},
        'formulario': {'id': fila[4], 'nombre': fila[5]},
        'autor':      {'id': fila[6], 'nombre': autor},
    }
    # No van las respuestas del formulario: si la URL quedó mal escrita,
    # lo que se filtra son nombres, no el contenido de la bitácora.
    return base


# ──────────────────────────── Entrega ────────────────────────────

def _anotar(cursor, webhook_id, empresa_id, mensaje, intento,
            estado_http=None, error=None, exitoso=False):
    cursor.execute("""
        INSERT INTO webhook_entregas
            (webhook_id, empresa_id, id_evento, evento,
             intento, estado_http, error, exitoso)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (webhook_id, empresa_id, mensaje['id_evento'], mensaje['evento'],
          intento, estado_http, (error or None), exitoso))


def _entregar(suscripcion, mensaje, empresa_id):
    """Envía con reintentos. Devuelve True si alguno fue aceptado."""
    from app import db_connection

    webhook_id, url, secreto_cifrado = suscripcion
    cuerpo = json.dumps(mensaje, ensure_ascii=False).encode('utf-8')

    try:
        secreto = descifrar_secreto(secreto_cifrado)
    except Exception as e:
        print(f'[WEBHOOK] No se pudo descifrar el secreto de {webhook_id}: {e}')
        return False

    for intento, espera in enumerate(ESPERAS, start=1):
        if espera:
            time.sleep(espera)

        estado, error = None, None
        try:
            # Se revalida el destino en cada intento: un dominio que ayer
            # resolvía a una IP pública puede resolver hoy a una interna.
            validar_url(url)

            marca = str(int(time.time()))
            respuesta = requests.post(
                url,
                data=cuerpo,
                timeout=TIMEOUT,
                allow_redirects=False,   # un 302 burlaría la validación
                headers={
                    'Content-Type':         'application/json; charset=utf-8',
                    'User-Agent':           'Bitacora-Digital-Webhook/1',
                    'X-Bitacora-Evento':    mensaje['evento'],
                    'X-Bitacora-Id-Evento': mensaje['id_evento'],
                    'X-Bitacora-Timestamp': marca,
                    'X-Bitacora-Intento':   str(intento),
                    'X-Bitacora-Firma':     firmar(secreto, marca, cuerpo),
                },
            )
            estado = respuesta.status_code
            exitoso = 200 <= estado < 300

        except ValueError as e:
            error, exitoso = str(e), False
        except Exception as e:
            error, exitoso = f'{type(e).__name__}: {e}', False

        try:
            with db_connection() as (conn, cursor):
                cursor.execute("SET app.empresa_id = %s", (str(empresa_id),))
                _anotar(cursor, webhook_id, empresa_id, mensaje,
                        intento, estado, error, exitoso)
        except Exception as e:
            print(f'[WEBHOOK] No se pudo anotar la entrega: {e}')

        if exitoso:
            return True

    return False


def _procesar(evento, empresa_id, ctx):
    """Busca a quién avisar, arma el mensaje y entrega."""
    import traceback
    from app import app, db_connection

    with _semaforo, app.app_context():
        try:
            proyecto_id = ctx.get('proyecto_id')

            with db_connection() as (conn, cursor):
                cursor.execute("SET app.empresa_id = %s", (str(empresa_id),))

                # Primero se mira si hay alguien escuchando: si no, no
                # tiene sentido pagar las consultas de nombres.
                cursor.execute("""
                    SELECT id, url, secreto
                    FROM webhooks
                    WHERE empresa_id = %s
                      AND activo
                      AND %s = ANY(eventos)
                      AND (proyectos IS NULL OR %s = ANY(proyectos))
                """, (empresa_id, evento, proyecto_id))
                suscripciones = cursor.fetchall()

                if not suscripciones:
                    return

                mensaje = _armar_mensaje(cursor, evento, empresa_id, ctx)
                if not mensaje:
                    print(f'[WEBHOOK] Sin datos para {evento}: {ctx}')
                    return

            for s in suscripciones:
                exitoso = _entregar(s, mensaje, empresa_id)

                with db_connection() as (conn, cursor):
                    cursor.execute("SET app.empresa_id = %s", (str(empresa_id),))
                    if exitoso:
                        cursor.execute("""
                            UPDATE webhooks
                            SET fallos_seguidos = 0, ultimo_envio_en = now()
                            WHERE id = %s
                        """, (s[0],))
                    else:
                        # Una URL que ya nadie atiende no puede seguir
                        # recibiendo intentos para siempre.
                        cursor.execute("""
                            UPDATE webhooks
                            SET fallos_seguidos = fallos_seguidos + 1,
                                ultimo_envio_en = now(),
                                activo = (fallos_seguidos + 1 < %s),
                                desactivado_en = CASE
                                    WHEN fallos_seguidos + 1 >= %s THEN now()
                                    ELSE desactivado_en END
                            WHERE id = %s
                        """, (FALLOS_PARA_DESACTIVAR,
                              FALLOS_PARA_DESACTIVAR, s[0]))

        except Exception:
            print(f'[WEBHOOK] Error procesando {evento}:')
            traceback.print_exc()


def lanzar_evento(evento, empresa_id, **ctx):
    """Punto de entrada. Se llama desde la petición y vuelve enseguida.

    Uso:
        lanzar_evento('registro.creado', empresa_id,
                      registro_id=57, proyecto_id=27, user_id=4)
    """
    if evento not in EVENTOS_VALIDOS:
        print(f'[WEBHOOK] Evento desconocido, se ignora: {evento}')
        return
    if not empresa_id:
        return

    hilo = threading.Thread(target=_procesar, daemon=True,
                            args=(evento, empresa_id, ctx))
    hilo.start()
