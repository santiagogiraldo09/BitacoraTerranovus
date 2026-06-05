from flask import Flask, request, jsonify, render_template, send_file, redirect,url_for, flash, jsonify
import azure.cognitiveservices.speech as speechsdk
from azure.storage.blob import BlobServiceClient,BlobClient,ContainerClient
from werkzeug.utils import secure_filename
import base64
import io
from io import BytesIO
from PIL import Image
import os
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from flask_cors import CORS
from datetime import datetime
from azure.storage.blob import ContentSettings
from dotenv import load_dotenv
from office365.sharepoint.client_context import ClientContext
from office365.runtime.auth.authentication_context import AuthenticationContext
from office365.sharepoint.lists.list import List
from office365.sharepoint.listitems.listitem import ListItem
import psycopg2
from werkzeug.security import generate_password_hash, check_password_hash
from flask import session
import secrets
from pydub import AudioSegment
import tempfile
import traceback
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image
import base64
import uuid
import json
import requests
from datetime import datetime
import pytz
from fpdf import FPDF
import io
from tempfile import NamedTemporaryFile
from supabase import create_client
from psycopg2 import pool as pg_pool
import time
from datetime import timezone
from contextlib import contextmanager
from datetime import timedelta
import secrets
import unicodedata
import re
from datetime import datetime, timedelta, timezone


connection_pool = None


SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)


# Configurar zona horaria
tijuana_tz = pytz.timezone('America/Tijuana')
fecha_hora_tijuana = datetime.now(tijuana_tz)

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

SYNCHRO_FORM_DEFINITION_ID = 'e4bQKVghekuuA8Y6dmHKWHlOJyH5vilDm9vLfuTg2mg'

DATABASE_URL = os.environ.get("DATABASE_URL")

POSTGRES_CONFIG = DATABASE_URL

# Configuración PostgreSQL
#POSTGRES_CONFIG = {
    #"host": "localhost",
    #"database": "Bitacora",
    #"user": "postgres",  # Normalmente 'postgres' por defecto
    #"password": "Daniel2030#",
    #"port": "5432"  # Puerto predeterminado de PostgreSQL
#}

SYNCHRO_CONFIG = {
    'client_id': 'service-o5fkAjNrOy3DBriRDwK4aA3Ud',
    'client_secret': 'VTkTyFi36+pUdJ/drZ5chOEhJufMuAZGofF9fzgg/SOUOkrPhPOZERxsq07FpleSZ0bBIRPJVjOua+bR4Exe3Q==',
    'token_url': 'https://ims.bentley.com/connect/token',
    'forms_url': 'https://api.bentley.com/forms',
    'itwin_id': '29d0867b-2158-4b7a-ae03-c63a7661ca58',
    'form_id': 'e4bQKVghekuuA8Y6dmHKWPFDh67WqydKr1vfz4Z0oAs'  # Formulario 1.09-00001
}

# Configura SharePoint (modifica con tus datos)
SHAREPOINT_SITE_URL = "https://iacsas.sharepoint.com/sites/Pruebasproyectossantiago"
LIST_NAME = "Proyectos"  # Nombre de la biblioteca
LIST_NAME_REGISTROS = "RegistrosBitacora"
SHAREPOINT_USER = "santiago.giraldo@iac.com.co"
SHAREPOINT_PASSWORD = "Latumbanuncamuere3"


# Cargar variables de entorno
#load_dotenv('config/settings.env')  # Ruta relativa al archivo .env

app = Flask(__name__,template_folder='templates')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

@app.before_request
def make_session_permanent():
    session.permanent = True
def init_pool():
    global connection_pool
    connection_pool = pg_pool.ThreadedConnectionPool(
        minconn=2,
        maxconn=10,
        dsn=os.environ.get('DATABASE_URL')
    )

init_pool()

@contextmanager
def db_connection():
    conn = None
    try:
        conn = connection_pool.getconn()
        cursor = conn.cursor()
        empresa_id = session.get('empresa_id', 1)
        cursor.execute("SET app.empresa_id = %s", (empresa_id,))
        yield conn, cursor
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            try:
                cursor.close()
            except:
                pass
            connection_pool.putconn(conn)
#app.secret_key = secrets.token_hex(16)  # Clave secreta para sesiones
app.secret_key = os.environ.get('SECRET_KEY', 'bitacora-iac-2026-fallback')
#app.secret_key = '78787878tyg8987652vgdfdf3445'
CORS(app)

from flask_mail import Mail, Message
import random, string

# Configuración de correo (ajusta con tu cuenta SMTP)
app.config['MAIL_SERVER']   = 'smtp.gmail.com'
app.config['MAIL_PORT']     = 587
app.config['MAIL_USE_TLS']  = True
app.config['MAIL_USERNAME'] = 'muneragacias@gmail.com'      # ← tu correo
app.config['MAIL_PASSWORD'] = 'rxghrdeqoupdkaex'         # ← contraseña de app Gmail
app.config['MAIL_DEFAULT_SENDER'] = 'muneragacias@gmail.com'

mail = Mail(app)

projects = []

# Conecta con el servicio de Blob Storage de Azure
connection_string = "DefaultEndpointsProtocol=https;AccountName=registrobitacora;AccountKey=ZyHZAOvOBijiOfY3BR3ZEDZsCAHOu3swEPnS+D7AacR2Yr94HS+jBMa2/20sJpZ71decGXYHQxE2+AStBWI/wA==;EndpointSuffix=core.windows.net"
container_name = "registros"


# Inicializa el cliente de BlobServiceClient
blob_service_client = BlobServiceClient.from_connection_string(connection_string)

def generar_password_temporal(longitud=10):
    caracteres = string.ascii_letters + string.digits
    return ''.join(random.choices(caracteres, k=longitud))


@app.route('/upload_foto', methods=['POST'])
def upload_foto():
    if 'user_id' not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        data = request.json
        file_data = data.get('file_data', '')

        # Quitar el prefijo data:image/...;base64,
        if ',' in file_data:
            header, b64 = file_data.split(',', 1)
            ext = 'png' if 'png' in header else 'jpg'
        else:
            b64, ext = file_data, 'jpg'

        imagen_bytes = base64.b64decode(b64)
        nombre_archivo = f"{uuid.uuid4()}.{ext}"
        ruta = f"registros/{nombre_archivo}"

        supabase_client.storage.from_('fotos-bitacora').upload(
            ruta,
            imagen_bytes,
            {"content-type": f"image/{ext}"}
        )

        url_publica = f"{SUPABASE_URL}/storage/v1/object/public/fotos-bitacora/{ruta}"
        return jsonify({"url": url_publica}), 200

    except Exception as e:
        print(f"Error subiendo foto: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/invitar-empresa', methods=['POST'])
def invitar_empresa():
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    if session.get('empresa_id') != 1:
        return jsonify({'error': 'No tienes permisos'}), 403

    data     = request.get_json()
    email    = data.get('email', '').strip()
    contacto = data.get('contacto', '').strip() or 'Cliente'

    if not email:
        return jsonify({'error': 'El correo es obligatorio'}), 400

    try:
        token     = secrets.token_urlsafe(32)
        expira_en = datetime.now(timezone.utc) + timedelta(days=7)
        link      = f"https://bitacoraiac.onrender.com/registroEmpresa?token={token}"

        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO tokens_registro (token, email, expira_en)
                VALUES (%s, %s, %s)
            """, (token, email, expira_en))

        msg      = Message(
            subject='Invitación para registrar tu empresa en Bitácora IAC',
            recipients=[email]
        )
        msg.html = f"""
        <div style="font-family:'DM Sans',Arial,sans-serif;max-width:560px;margin:auto;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">

            <!-- Header -->
            <div style="background:#0f0f0f;padding:32px 40px;text-align:center;">
                <img src="https://bitacoraiac.onrender.com/static/LogoBlancoIAC.png"
                     style="height:52px;border-radius:10px;margin-bottom:16px;" alt="IAC">
                <h1 style="color:#FFAF33;font-size:22px;margin:0;font-weight:800;letter-spacing:-0.5px;">
                    Bitácora IAC
                </h1>
                <p style="color:#9ca3af;font-size:13px;margin:8px 0 0;">
                    Plataforma de gestión de proyectos en campo
                </p>
            </div>

            <!-- Cuerpo -->
            <div style="padding:40px;">
                <p style="font-size:16px;color:#1a1a1a;margin:0 0 8px;">
                    Hola, <strong>{contacto}</strong>
                </p>
                <p style="font-size:15px;color:#4b5563;line-height:1.6;margin:0 0 28px;">
                    <strong>IAC — Ingeniería Asistida por Computador</strong> te ha invitado a registrar
                    tu empresa en <strong>Bitácora IAC</strong>, la plataforma para gestión de
                    actividades y contactos en campo.
                </p>

                <!-- Pasos -->
                <div style="background:#f9fafb;border-radius:12px;padding:24px;margin-bottom:28px;">
                    <p style="font-size:13px;font-weight:700;color:#6b7280;letter-spacing:0.05em;margin:0 0 16px;">
                        ¿QUÉ INCLUYE TU CUENTA?
                    </p>
                    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
                        <div style="width:32px;height:32px;border-radius:8px;background:#fff8ee;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                            <span style="color:#FFAF33;font-size:16px;">🏢</span>
                        </div>
                        <span style="font-size:14px;color:#374151;">Espacio exclusivo para tu empresa</span>
                    </div>
                    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
                        <div style="width:32px;height:32px;border-radius:8px;background:#fff8ee;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                            <span style="color:#FFAF33;font-size:16px;">👥</span>
                        </div>
                        <span style="font-size:14px;color:#374151;">Invita a todo tu equipo de trabajo</span>
                    </div>
                    <div style="display:flex;align-items:center;gap:12px;">
                        <div style="width:32px;height:32px;border-radius:8px;background:#fff8ee;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                            <span style="color:#FFAF33;font-size:16px;">🎙️</span>
                        </div>
                        <span style="font-size:14px;color:#374151;">Registro por voz y captura de evidencias</span>
                    </div>
                </div>

                <!-- Botón CTA -->
                <div style="text-align:center;margin-bottom:28px;">
                    <a href="{link}"
                       style="display:inline-block;background:#FFAF33;color:#ffffff;
                              padding:16px 40px;border-radius:10px;text-decoration:none;
                              font-size:16px;font-weight:700;letter-spacing:0.02em;">
                        Registrar mi empresa →
                    </a>
                </div>

                <!-- Nota de expiración -->
                <div style="background:#fff8ee;border:1px solid #fed7aa;border-radius:8px;padding:14px 16px;margin-bottom:24px;">
                    <p style="font-size:13px;color:#92400e;margin:0;">
                        ⏳ <strong>Este enlace expira en 7 días.</strong>
                        Si necesitas uno nuevo, contacta a IAC.
                    </p>
                </div>

                <p style="font-size:12px;color:#9ca3af;line-height:1.5;margin:0;">
                    Si no esperabas este correo, puedes ignorarlo de forma segura.
                    El enlace solo funciona una vez y expira automáticamente.
                </p>
            </div>

            <!-- Footer -->
            <div style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;text-align:center;">
                <p style="font-size:12px;color:#6b7280;margin:0;">
                    © 2026 IAC — Ingeniería Asistida por Computador
                </p>
                <p style="font-size:12px;color:#9ca3af;margin:6px 0 0;">
                    <a href="https://iac.com.co" style="color:#FFAF33;text-decoration:none;">iac.com.co</a>
                </p>
            </div>

        </div>
        """
        mail.send(msg)

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error enviando invitación empresa: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/invitar-usuarios', methods=['POST'])
def invitar_usuarios():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'No autenticado'}), 401

    data     = request.get_json()
    personas = data.get('personas', [])

    if not personas:
        return jsonify({'success': False, 'error': 'No se recibieron datos'})

    conn = None
    try:
        conn, cursor = get_db_connection()
        empresa_id = session.get('empresa_id')

        cursor.execute("""
            SELECT name, empresa_id FROM usuario WHERE user_id = %s
        """, (session['user_id'],))
        admin      = cursor.fetchone()
        admin_nombre = admin[0] if admin else 'El administrador'

        enviados = []
        omitidos = []

        for p in personas:
            nombre   = p.get('nombre', '')
            apellido = p.get('apellido', '')
            correo   = p.get('correo', '')
            cargo    = p.get('cargo', 'Sin asignar')
            rol      = p.get('rol', 'viewer')

            if not nombre or not apellido or not correo:
                omitidos.append(correo or 'sin correo')
                continue

            cursor.execute(
                "SELECT user_id FROM usuario WHERE email = %s", (correo,)
            )
            if cursor.fetchone():
                omitidos.append(correo)
                continue

            password_temp = generar_password_temporal()
            hashed        = generate_password_hash(password_temp)

            cursor.execute("""
                INSERT INTO usuario
                    (name, apellido, email, password, cargo, rol, empresa_id, estado)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pendiente')
            """, (nombre, apellido, correo, hashed, cargo, rol, empresa_id))

            try:
                msg = Message(
                    subject=f'Invitación — Bitácora App',
                    recipients=[correo]
                )
                msg.html = f"""
                <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;
                            padding:32px;background:#fff;border-radius:12px;border:1px solid #eee;">
                    <h2 style="color:#1A1A2E;margin:0 0 8px;">Has sido invitado</h2>
                    <p style="color:#555;font-size:15px;margin:0 0 24px;">
                        <strong>{admin_nombre}</strong> te ha invitado a unirte a Bitácora App.
                    </p>
                    <div style="background:#F5F6FA;border-radius:10px;padding:20px;margin-bottom:24px;">
                        <p style="margin:0 0 8px;color:#888;font-size:13px;">TUS CREDENCIALES</p>
                        <p style="margin:0 0 4px;font-size:15px;">
                            <strong>Correo:</strong> {correo}
                        </p>
                        <p style="margin:0;font-size:15px;">
                            <strong>Contraseña temporal:</strong>
                            <span style="font-family:monospace;background:#fff;padding:2px 8px;
                                         border-radius:4px;border:1px solid #ddd;">
                                {password_temp}
                            </span>
                        </p>
                    </div>
                    <p style="color:#e09a1f;font-size:13px;margin:0 0 24px;">
                        ⚠️ Cambia tu contraseña después de ingresar por primera vez.
                    </p>
                    <a href="https://bitacoraiac.onrender.com"
                       style="display:block;text-align:center;background:#FBAF33;color:#fff;
                              padding:14px;border-radius:8px;text-decoration:none;
                              font-weight:bold;font-size:16px;">
                        Ingresar a la app
                    </a>
                </div>
                """
                mail.send(msg)
            except Exception as mail_err:
                print(f"Error enviando correo a {correo}: {mail_err}")

            enviados.append(correo)

        conn.commit()

        mensaje = f'Invitación enviada a {len(enviados)} persona(s).'
        if omitidos:
            mensaje += f' {len(omitidos)} omitido(s) por ya existir.'

        return jsonify({
            'success': True,
            'enviados': len(enviados),
            'omitidos': omitidos,
            'mensaje':  mensaje
        })

    except Exception as e:
        print(f"Error en invitar_usuarios: {e}")
        if conn: conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

# ── Utilidad: generar slug ──────────────────────────────────────
def generar_slug(nombre_empresa):
    # Normalizar: quitar tildes, minúsculas, reemplazar espacios
    nfkd = unicodedata.normalize('NFD', nombre_empresa)
    sin_tildes = ''.join(c for c in nfkd if not unicodedata.combining(c))
    slug = sin_tildes.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug.strip())
    return slug
 
 
# ── Ruta GET: mostrar formulario ────────────────────────────────
@app.route('/registroEmpresa', methods=['GET'])
def registro_page():
    token = request.args.get('token', '')
 
    if not token:
        return render_template('registroEmpresa.html', token_valido=False, token='', email_sugerido='')
 
    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT id, email, usado, expira_en
                FROM tokens_registro
                WHERE token = %s
            """, (token,))
            row = cursor.fetchone()
 
        if not row:
            return render_template('registroEmpresa.html', token_valido=False, token='', email_sugerido='')
 
        _, email_sugerido, usado, expira_en = row
 
        if usado:
            return render_template('registroEmpresa.html', token_valido=False, token='', email_sugerido='')
 
        # Verificar expiración
        ahora = datetime.now(timezone.utc)
        if expira_en.tzinfo is None:
            expira_en = expira_en.replace(tzinfo=timezone.utc)
 
        if ahora > expira_en:
            return render_template('registroEmpresa.html', token_valido=False, token='', email_sugerido='')
 
        return render_template('registroEmpresa.html',
                               token_valido=True,
                               token=token,
                               email_sugerido=email_sugerido or '')
 
    except Exception as e:
        print(f"Error validando token: {e}")
        return render_template('registroEmpresa.html', token_valido=False, token='', email_sugerido='')
 
 
# ── Ruta POST: procesar registro ────────────────────────────────
@app.route('/registroEmpresa', methods=['POST'])
def registro_post():
    data     = request.get_json()
    token    = data.get('token', '')
    nombre   = data.get('nombre', '').strip()
    apellido = data.get('apellido', '').strip()
    email    = data.get('email', '').strip()
    password = data.get('password', '')
    cargo    = data.get('cargo', '').strip()
    empresa  = data.get('empresa', '').strip()
 
    if not all([token, nombre, apellido, email, password, empresa]):
        return jsonify({'error': 'Faltan campos obligatorios'}), 400
 
    if len(password) < 8:
        return jsonify({'error': 'La contraseña debe tener al menos 8 caracteres'}), 400
 
    try:
        with db_connection() as (conn, cursor):
 
            # 1. Validar token
            cursor.execute("""
                SELECT id, usado, expira_en
                FROM tokens_registro
                WHERE token = %s
            """, (token,))
            row = cursor.fetchone()
 
            if not row:
                return jsonify({'error': 'Token inválido'}), 400
 
            token_id, usado, expira_en = row
 
            if usado:
                return jsonify({'error': 'Este enlace ya fue utilizado'}), 400
 
            ahora = datetime.now(timezone.utc)
            if expira_en.tzinfo is None:
                expira_en = expira_en.replace(tzinfo=timezone.utc)
 
            if ahora > expira_en:
                return jsonify({'error': 'Este enlace ha expirado'}), 400
 
            # 2. Verificar que el email no exista
            cursor.execute(
                "SELECT user_id FROM usuario WHERE email = %s", (email,)
            )
            if cursor.fetchone():
                return jsonify({'error': 'Este correo ya está registrado'}), 400
 
            # 3. Generar slug único para la empresa
            slug_base = generar_slug(empresa)
            slug      = slug_base
            contador  = 1
            while True:
                cursor.execute(
                    "SELECT id FROM empresas WHERE slug = %s", (slug,)
                )
                if not cursor.fetchone():
                    break
                slug = f"{slug_base}-{contador}"
                contador += 1
 
            # 4. Crear empresa
            cursor.execute("""
                INSERT INTO empresas (nombre, slug)
                VALUES (%s, %s)
                RETURNING id
            """, (empresa, slug))
            empresa_id = cursor.fetchone()[0]
 
            # 5. Crear usuario admin
            hashed = generate_password_hash(password)
            cursor.execute("""
                INSERT INTO usuario
                    (name, apellido, email, password, cargo, rol, empresa_id, estado)
                VALUES (%s, %s, %s, %s, %s, 'admin', %s, 'activo')
                RETURNING user_id
            """, (nombre, apellido, email, hashed, cargo or 'Administrador', empresa_id))

            nuevo_user_id = cursor.fetchone()[0]

            session['user_id']    = nuevo_user_id
            session['user_rol']   = 'admin'
            session['empresa_id'] = empresa_id
            session['user_name']  = nombre
 
            # 6. Marcar token como usado
            cursor.execute("""
                UPDATE tokens_registro
                SET usado = TRUE
                WHERE id = %s
            """, (token_id,))

        # Login automático después del registro
        session['user_id']    = cursor.lastrowid if hasattr(cursor, 'lastrowid') else None
        session['user_rol']   = 'admin'
        session['empresa_id'] = empresa_id
        session['user_name']  = nombre
        return jsonify({'success': True})
 
    except Exception as e:
        print(f"Error en registro: {e}")
        return jsonify({'error': str(e)}), 500
 
 
# ── Ruta: generar token de invitación (solo admins IAC) ─────────
@app.route('/generar-token-registro', methods=['POST'])
def generar_token_registro():
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401
 
    # Solo el admin de IAC (empresa_id = 1) puede generar tokens
    if session.get('empresa_id') != 1:
        return jsonify({'error': 'No tienes permisos para esta acción'}), 403
 
    data  = request.get_json()
    email = data.get('email', '').strip()
 
    if not email:
        return jsonify({'error': 'El correo es obligatorio'}), 400
 
    try:
        token     = secrets.token_urlsafe(32)
        expira_en = datetime.now(timezone.utc) + timedelta(days=7)
 
        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO tokens_registro (token, email, expira_en)
                VALUES (%s, %s, %s)
            """, (token, email, expira_en))
 
        link = f"https://bitacoraiac.onrender.com/registroEmpresa?token={token}"
 
        return jsonify({
            'success': True,
            'token':   token,
            'link':    link,
            'expira':  expira_en.strftime('%d/%m/%Y %H:%M')
        })
 
    except Exception as e:
        print(f"Error generando token: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# OBTENER TOKEN DE BENTLEY
# ========================================
def obtener_token_synchro():
    """Obtiene token de acceso de Bentley IMS"""
    try:
        payload = {
            'grant_type': 'client_credentials',
            'client_id': SYNCHRO_CONFIG['client_id'],
            'client_secret': SYNCHRO_CONFIG['client_secret'],
            'scope': 'itwin-platform'
        }
        
        response = requests.post(SYNCHRO_CONFIG['token_url'], data=payload, timeout=10)
        
        if response.status_code == 200:
            token = response.json().get('access_token')
            print("✅ Token obtenido")
            return token
        else:
            print(f"❌ Error obteniendo token: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ Excepción: {str(e)}")
        return None

# ========================================
# ENVIAR ACTIVIDADES A SYNCHRO
# ========================================
def enviar_actividades_synchro(token, data):
    """Envía todas las actividades al formulario de Synchro"""
    try:
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.bentley.itwin-platform.v2+json',
            'Prefer': 'return=representation',
            'Content-Type': 'application/json'
        }
        
        # 1. Obtener formulario actual
        url_form = f"{SYNCHRO_CONFIG['forms_url']}/{SYNCHRO_CONFIG['form_id']}"
        response = requests.get(url_form, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return {'success': False, 'error': f'No se pudo obtener formulario: {response.status_code}'}
        
        form_actual = response.json().get('form', {})
        props = form_actual.get('properties', {})
        
        # 2. Actualizar propiedades básicas
        props['Codigo Proyecto'] = data['codigo_proyecto']
        props['Contratista'] = data['contratista']
        props['Contrato'] = data['contrato']
        
        # 3. Sección 1: Actividades finalizadas
        actividades_finalizadas = props.get('Actividades finalizadas', [])
        for act in data.get('actividades_finalizadas', []):
            nueva_act = {
                'id': str(uuid.uuid4()),
                '__x00cd__tem': act['item'],
                'Descripci__x00f3__n': act['descripcion'],
                'Observaciones__x0020__actividades__x': act['observaciones']
            }
            actividades_finalizadas.append(nueva_act)
        props['Actividades finalizadas'] = actividades_finalizadas
        
        # 4. Sección 2: Actividades pendientes por culminar
        actividades_pendientes = props.get('Actividades pendientes', [])
        for act in data.get('actividades_pendientes', []):
            nueva_act = {
                'id': str(uuid.uuid4()),
                '__x00cd__tem__x0020__Pendiente': act['item'],
                'Descripci__x00f3__n__x0020__pendient': act['descripcion'],
                'Pendiente__x0020__generado': act.get('pendiente_generado', ''),
                'Observaciones__x0020__pendientes': act['observaciones']
            }
            actividades_pendientes.append(nueva_act)
        props['Actividades pendientes'] = actividades_pendientes
        
        # 5. Sección 3: Actividades pendientes por facturar
        # Nota: Necesitarás el nombre exacto de este campo en Synchro
        # Por ahora lo dejo como ejemplo
        if 'actividades_facturar' in data and data['actividades_facturar']:
            actividades_facturar = props.get('Actividades pendientes por facturar', [])
            for act in data['actividades_facturar']:
                nueva_act = {
                    'id': str(uuid.uuid4()),
                    '__x00cd__tem': act['item'],
                    'Descripci__x00f3__n': act['descripcion'],
                    'Cantidad_contractual': act['cantidad_contractual'],
                    'Cantidad_facturada': act['cantidad_facturada'],
                    'Cantidad_pendiente': act['cantidad_pendiente'],
                    'Observaci__x00f3__n': act['observacion']
                }
                actividades_facturar.append(nueva_act)
            props['Actividades pendientes por facturar'] = actividades_facturar
        
        # 6-8. Secciones de documentación (similar estructura)
        # Agregar según los nombres exactos de los campos en Synchro
        
        # 9. Enviar actualización
        cambios = {'properties': props}
        response_update = requests.patch(url_form, headers=headers, json=cambios, timeout=15)
        
        if response_update.status_code == 200:
            print("✅ Formulario actualizado en Synchro")
            return {'success': True, 'form_id': SYNCHRO_CONFIG['form_id']}
        else:
            error_msg = response_update.text
            print(f"❌ Error actualizando: {error_msg}")
            return {'success': False, 'error': error_msg}
            
    except Exception as e:
        print(f"❌ Excepción: {str(e)}")
        return {'success': False, 'error': str(e)}

# ========================================
# SUBIR ATTACHMENTS A SYNCHRO
# ========================================
def subir_attachments_synchro(token, fotos, videos):
    """Sube fotos y videos como adjuntos al formulario"""
    try:
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.bentley.itwin-platform.v2+json'
        }
        
        url_attachments = f"{SYNCHRO_CONFIG['forms_url']}/{SYNCHRO_CONFIG['form_id']}/attachments"
        
        contador = 0
        
        # Subir fotos
        for i, foto_base64 in enumerate(fotos[:10]):  # Máximo 10 fotos
            try:
                if ',' in foto_base64:
                    foto_base64 = foto_base64.split(',')[1]
                
                foto_bytes = base64.b64decode(foto_base64)
                
                files = {
                    'file': (f'foto_{i+1}.jpg', io.BytesIO(foto_bytes), 'image/jpeg')
                }
                
                data = {
                    'caption': f'Foto {i+1} - Evidencia'
                }
                
                response = requests.post(url_attachments, headers=headers, files=files, data=data, timeout=30)
                
                if response.status_code == 201:
                    contador += 1
                    print(f"✅ Foto {i+1} subida")
                else:
                    print(f"⚠️ Error subiendo foto {i+1}")
                    
            except Exception as e:
                print(f"⚠️ Error procesando foto {i+1}: {str(e)}")
                continue
        
        # Subir videos
        for i, video_base64 in enumerate(videos[:5]):  # Máximo 5 videos
            try:
                if ',' in video_base64:
                    video_base64 = video_base64.split(',')[1]
                
                video_bytes = base64.b64decode(video_base64)
                
                files = {
                    'file': (f'video_{i+1}.webm', io.BytesIO(video_bytes), 'video/webm')
                }
                
                data = {
                    'caption': f'Video {i+1} - Evidencia'
                }
                
                response = requests.post(url_attachments, headers=headers, files=files, data=data, timeout=60)
                
                if response.status_code == 201:
                    contador += 1
                    print(f"✅ Video {i+1} subido")
                else:
                    print(f"⚠️ Error subiendo video {i+1}")
                    
            except Exception as e:
                print(f"⚠️ Error procesando video {i+1}: {str(e)}")
                continue
        
        return contador
        
    except Exception as e:
        print(f"❌ Error en subir_attachments: {str(e)}")
        return 0


@app.route('/guardar-formulario', methods=['POST'])
def guardar_formulario():
    """Recibe datos del frontend y los envía a Synchro"""
    try:
        data = request.json
        print("📥 Datos recibidos del frontend")
        
        # Validar que al menos venga UNA sección con datos
        secciones_con_datos = sum([
            1 if data.get('actividades_finalizadas') else 0,
            1 if data.get('actividades_pendientes') else 0,
            1 if data.get('actividades_facturar') else 0,
            1 if data.get('documentacion_seguridad') else 0,
            1 if data.get('documentacion_ambiental') else 0,
            1 if data.get('documentacion_calidad') else 0
        ])
        
        if secciones_con_datos == 0:
            return jsonify({
                'success': False,
                'error': 'Debes llenar al menos una sección del formulario'
            }), 400
        
        print(f"✅ Validación OK: {secciones_con_datos} sección(es) con datos")
        
        # 1. Obtener token
        token = obtener_token_synchro()
        if not token:
            return jsonify({
                'success': False,
                'error': 'No se pudo obtener token de Synchro'
            }), 500
        
        # 2. Enviar actividades a Synchro
        resultado = enviar_actividades_synchro(token, data)
        if not resultado['success']:
            return jsonify(resultado), 500
        
        # 3. Subir fotos/videos si existen
        fotos = data.get('fotos', [])
        videos = data.get('videos', [])
        attachments_subidos = 0
        
        if fotos or videos:
            attachments_subidos = subir_attachments_synchro(token, fotos, videos)
        
        # 4. Retornar éxito
        return jsonify({
            'success': True,
            'mensaje': 'Registro guardado en Synchro exitosamente',
            'form_id': SYNCHRO_CONFIG['form_id'],
            'attachments_subidos': attachments_subidos,
            'secciones_guardadas': secciones_con_datos
        }), 200
        
    except Exception as e:
        print(f"❌ Error en /guardar-formulario: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

'''
def create_user(nombre, apellido, email, password, cargo, rol, empresa):
    try:
        conn, cursor = get_db_connection()
        
        hashed_password = generate_password_hash(password)
        
        cursor.execute(
            """INSERT INTO usuario (name, apellido, email, password, cargo, rol, empresa)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING user_id""",
            (nombre, apellido, email, hashed_password, cargo, rol, empresa)
        )
        
        user_id = cursor.fetchone()[0]
        conn.commit()
        return user_id
    except psycopg2.Error as e:
        print(f"Error al crear usuario: {e}")
        return None
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)
'''

def verify_user(email, password):
    conn = None
    try:
        conn = connection_pool.getconn()
        cursor = conn.cursor()
        # Sin SET empresa_id porque aún no hay sesión
        cursor.execute(
            "SELECT user_id, password, rol, empresa_id, name, estado FROM usuario WHERE email = %s",
            (email,)
        )
        user = cursor.fetchone()
        if user and check_password_hash(user[1], password):
            return {
                'user_id':    user[0],
                'rol':        user[2],
                'empresa_id': user[3],
                'name':       user[4],
                'estado':     user[5]
            }
        return None
    except Exception as e:
        print(f"Error al verificar usuario: {e}")
        return None
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

def insert_registro_bitacora(respuestas, id_proyecto, fotos=None, videos=None):
    """
    Inserta un nuevo registro de bitácora, junto con sus fotos y videos asociados
    y sus descripciones, en la base de datos.
    """
    conn = None  # Definimos conn aquí para asegurarnos de que exista en el bloque finally
    try:
        conn, cursor = get_db_connection()

        # CAMBIO 1: Simplificamos el INSERT principal.
        # - Eliminamos la columna 'foto_base64' que ya es obsoleta.
        # - Cambiamos los nombres de las claves para que coincidan con tu formulario.
        cursor.execute("""
            INSERT INTO registrosbitacoraeqing (
                zona_intervencion, -- Mapeado desde "Tipo de informe"
                items,             -- Mapeado desde "Sede"
                metros_lineales,   -- Mapeado desde "Repuestos utilizados"
                proximas_tareas,   -- Mapeado desde "Repuestos a cotizar"
                id_proyecto
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_registro
        """, (
            respuestas.get('zona_intervencion'),
            respuestas.get('items'),
            respuestas.get('metros_lineales'),
            respuestas.get('proximas_tareas'),
            id_proyecto,
        ))
        id_registro = cursor.fetchone()[0]

        # CAMBIO 2: Actualizamos el bucle para que maneje objetos (archivo + descripción).
        # Ahora esperamos una lista de diccionarios, no solo una lista de strings.
        for foto_obj in fotos or []:
            file_data = foto_obj.get('file_data')
            description = foto_obj.get('description')
            cursor.execute(
                """INSERT INTO fotos_registro 
                   (id_registro, imagen_base64, description) 
                   VALUES (%s, %s, %s)""",
                (id_registro, file_data, description)
            )

        # CAMBIO 3: Hacemos lo mismo para los videos.
        for video_obj in videos or []:
            file_data = video_obj.get('file_data')
            description = video_obj.get('description')
            cursor.execute(
                """INSERT INTO videos_registro 
                   (id_registro, video_base64, description) 
                   VALUES (%s, %s, %s)""",
                (id_registro, file_data, description)
            )

        conn.commit()
        print(f"Registro {id_registro} guardado exitosamente en PostgreSQL.")

    except psycopg2.Error as e: # MEJORA: Capturamos el error específico de psycopg2 para más detalles
        print(f"Error de base de datos al guardar en PostgreSQL: {e}")
        # Opcional: podrías querer que la función devuelva un error
        # raise e 
    except Exception as e:
        print(f"Error general al guardar en PostgreSQL: {str(e)}")
        # raise e
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

def create_project(user_id, nombre, fecha_inicio, fecha_fin, director, ubicacion, coordenadas, cliente, numero_proyecto):
    try:
        conn, cursor = get_db_connection()
        
        cursor.execute(
            """INSERT INTO proyectos (nombre_proyecto, fecha_inicio, fecha_fin, director_obra, ubicacion, coordenadas, user_id, cliente, numero_proyecto)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id_proyecto""",
            (nombre, fecha_inicio, fecha_fin, director, ubicacion, coordenadas, user_id, cliente, numero_proyecto)
        )
        
        project_id = cursor.fetchone()[0]
        conn.commit()
        return project_id
    except psycopg2.Error as e:
        print(f"Error al crear proyecto: {e}")
        return None
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

def get_db_connection():
    conn = connection_pool.getconn()
    cursor = conn.cursor()
    empresa_id = session.get('empresa_id', 1)
    cursor.execute("SET app.empresa_id = %s", (empresa_id,))
    print(f"[POOL] Conexiones en uso: {len(connection_pool._used)}")
    return conn, cursor

'''
def get_db_connection():
    conn = psycopg2.connect(POSTGRES_CONFIG)
    cursor = conn.cursor()
    empresa_id = session.get('empresa_id', 1)
    cursor.execute("SET app.empresa_id = %s", (empresa_id,))
    return conn, cursor
'''

def get_user_projects(user_id):
    try:
        with db_connection() as (conn, cursor):
        
            cursor.execute("""
                SELECT 
                    p.id, 
                    p.nombre_proyecto, 
                    p.fecha_inicio, 
                    p.cliente, 
                    p.user_id,
                    p.estado,
                    COUNT(c.id) as total_registros
                FROM proyectos p
                INNER JOIN proyecto_usuarios pu ON pu.id_proyecto = p.id
                LEFT JOIN contactos c ON c.id_proyecto = p.id
                WHERE pu.user_id = %s 
                GROUP BY p.id, p.nombre_proyecto, 
                        p.fecha_inicio, p.cliente, p.user_id, p.estado
                ORDER BY p.fecha_inicio DESC
            """, (user_id,)
            )

            '''
            cursor.execute("""
                SELECT 
                    p.id, 
                    p.nombre_proyecto, 
                    p.fecha_inicio, 
                    p.cliente, 
                    p.user_id,
                    p.estado,
                    COUNT(r.id) as total_registros
                FROM proyectos p
                INNER JOIN proyecto_usuarios pu ON pu.id_proyecto = p.id
                LEFT JOIN registros r ON r.id_proyecto = p.id
                WHERE pu.user_id = %s 
                GROUP BY p.id, p.nombre_proyecto, 
                        p.fecha_inicio, p.cliente, p.user_id, p.estado
                ORDER BY p.fecha_inicio DESC
            """, (user_id,)
            )
            '''
            
            projects = []
            for row in cursor.fetchall():
                projects.append({
                    'id_proyecto':      row[0],
                    'name':             row[1],
                    'fecha_inicio':     row[2].strftime('%Y-%m-%d'),
                    'cliente':          row[3],
                    'user_id':          row[4],
                    'estado':           row[5] or 'En Curso',
                    'total_registros':  row[6],
                })
            
            return projects
    except Exception as e:
        print(f"Error al obtener proyectos: {e}")
        return []

# Función para subir archivos a Azure Blob Storage
def upload_to_blob(file_name, data, content_type):
    try:
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=file_name)
        blob_client.upload_blob(data, blob_type="BlockBlob", content_settings={"content_type": content_type})
        print(f"Archivo {file_name} subido con éxito.")
    except Exception as e:
        print(f"Error al subir {file_name}: {e}")
        raise


def get_speech_config():
    speech_key = '999fcb4d3f34436ab454ec47920febe0'
    service_region = 'centralus'
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=service_region)
    speech_config.speech_recognition_language = "es-CO"
    speech_config.speech_synthesis_language = "es-CO"
    speech_config.speech_synthesis_voice_name = "es-CO-GonzaloNeural"
    speech_config.set_property(speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "8000")

    # Esto le pide a Azure que formatee el texto, convirtiendo palabras como "cinco" a "5".
    speech_config.set_property(speechsdk.PropertyId.SpeechServiceResponse_PostProcessingOption, "TrueText")

    return speech_config

def synthesize_speech(text):
    speech_config = get_speech_config()
    audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    result = synthesizer.speak_text_async(text).get()
    return result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted

#Obtener los proyectos desde Azure Blob Storage
def get_projects_from_blob():
    projects = []
    try:
        # Obtener el cliente del contenedor
        container_client = blob_service_client.get_container_client(container_name)
        
        # Listar los blobs en el directorio de proyectos
        blobs = list(container_client.list_blobs(name_starts_with="Proyectos/"))
        
        for blob in blobs:
            if blob.name.endswith('.txt'):
                # Obtener el cliente del blob
                blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob.name)
                
                # Descargar el contenido del blob
                content = blob_client.download_blob().readall().decode('utf-8')
                
                # Extraer información del proyecto
                project_info = {}
                for line in content.strip().split('\n'):
                    line = line.strip()
                    if line:
                        parts = line.split(':', 1)
                        if len(parts) == 2:
                            key = parts[0].strip()
                            value = parts[1].strip()
                            project_info[key] = value
                
                # Extraer el nombre del proyecto del nombre del archivo
                file_name = blob.name.split('/')[-1]
                project_name = file_name.replace('proyecto_', '').replace('.txt', '')
                
                # Crear un objeto de proyecto
                project = {
                    'name': project_info.get('Nombre del Proyecto', project_name),
                    'date': project_info.get('Fecha de Inicio', 'Fecha no disponible'),
                    'blob_name': blob.name,
                    # Añadir más campos según sea necesario
                }
                
                projects.append(project)
                
    except Exception as e:
        print(f"Error al obtener proyectos del Blob Storage: {e}")
    
    return projects

@app.after_request
def add_header(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

@app.route('/')
def principalscreen():
    return render_template('PrincipalScreen.html')

@app.route('/paginaprincipal')
def paginaprincipal():
    project_id = request.args.get('project_id')
    project_name = request.args.get('project')

    if not project_id:
        return redirect(url_for('history')) # <--- AQUÍ ES DONDE TE ESTÁ MANDANDO

    try:
        conn, cursor = get_db_connection()
        
        # ASEGÚRATE DE QUE ESTE QUERY USE LA TABLA NUEVA
        cursor.execute('SELECT * FROM proyectos WHERE id_proyecto = %s', (project_id,))
        proyecto = cursor.fetchone()
        
        if not proyecto:
            # Si el ID existe en la URL pero no en la tabla, te manda a history
            return redirect(url_for('history')) 

        return render_template('paginaprincipal.html', project_id=project_id, project_name=project_name)
    except Exception as e:
        print(f"Error: {e}")
        return redirect(url_for('history'))

'''
@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        apellido = request.form.get('apellido')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        empresa = request.form.get('empresa')
        cargo = request.form.get('cargo')
        rol = request.form.get('rol')
        
        if password != confirm_password:
            flash('Las contraseñas no coinciden', 'error')
            return redirect(url_for('registro'))
        
        user_id = create_user(nombre, apellido, email, password, cargo, rol, empresa)
        if user_id:
            flash('Registro exitoso. Por favor inicie sesión.', 'success')
            return redirect(url_for('principalscreen'))
        else:
            flash('Error al registrar el usuario', 'error')
    
    return render_template('registro.html')
'''

@app.route('/login', methods=['POST'])
def login():
    t0 = time.time()
    email    = request.form.get('email')
    password = request.form.get('password')

    if not email or not password:
        return jsonify({'error': 'Por favor ingrese ambos campos'}), 400

    t1 = time.time()
    usuario = verify_user(email, password)
    print(f"verify_user tardó: {time.time() - t1:.2f}s")

    if usuario:
        session['user_id']    = usuario['user_id']
        session['user_rol']   = usuario['rol']
        session['empresa_id'] = usuario['empresa_id']
        session['user_name']  = usuario['name']

        print(f"Login total tardó: {time.time() - t0:.2f}s")

        # Si es pendiente, redirigir a cambiar contraseña
        if usuario.get('estado') == 'pendiente':
            return redirect(url_for('cambiar_password_page'))

        return redirect(url_for('registros'))
    else:
        return jsonify({'error': 'Credenciales incorrectas'}), 401


@app.route('/check-session')
def check_session():
    if 'user_id' not in session:
        return jsonify({'redirect': '/'})
    
    try:
        with db_connection() as (conn, cursor):
            cursor.execute(
                "SELECT estado FROM usuario WHERE user_id = %s",
                (session['user_id'],)
            )
            row = cursor.fetchone()
            estado = row[0] if row else 'activo'

        if estado == 'pendiente':
            return jsonify({'redirect': '/cambiar-password'})
        return jsonify({'redirect': '/registros'})

    except Exception as e:
        return jsonify({'redirect': '/registros'})
        
"""
@app.route('/index')
def index():
    return render_template('index.html')
"""

@app.route('/index')
def index():
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
    
    project_id = request.args.get('project_id')
    project_info = None
    
    if project_id:
        try:
            conn, cursor = get_db_connection()
            # Consultamos las columnas exactas de tu tabla según las imágenes
            cursor.execute("""
                SELECT nombre_proyecto, cliente, contratista, orden_de_trabajo, ubicacion 
                FROM proyectos 
                WHERE id = %s
            """, (project_id,))
            row = cursor.fetchone()
            if row:
                project_info = {
                    'nombre': row[0],
                    'cliente': row[1],
                    'contratista': row[2],
                    'orden_de_trabajo': row[3],
                    'ubicacion': row[4]
                }
            conn.close()
        except Exception as e:
            print(f"Error al obtener info del proyecto: {e}")

    return render_template('index.html', project=project_info)

def obtener_token():
    """Obtiene un token de autenticación de Bentley."""
    try:
        payload = {
            'grant_type': 'client_credentials',
            'client_id': SYNCHRO_CONFIG['client_id'],
            'client_secret': SYNCHRO_CONFIG['client_secret'],
            'scope': 'itwin-platform'
        }
        response = requests.post(SYNCHRO_CONFIG['token_url'], data=payload)
        
        if response.status_code == 200:
            return response.json()['access_token']
        else:
            print(f"Error al obtener token (código {response.status_code}): {response.text}")
            return None
    except Exception as e:
        print(f"Excepción al obtener token: {str(e)}")
        return None
    
def obtener_id_por_numero(token, numero):
    """Busca un formulario por su número y retorna su ID y el objeto 'form' completo."""
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.bentley.itwin-platform.v2+json',
        'Prefer': 'return=representation'
    }
    
    url = SYNCHRO_CONFIG['forms_url']
    params = {
        'iTwinId': SYNCHRO_CONFIG['itwin_id'],
        '$top': 50  # Obtener de 50 en 50
    }
    
    while True:
        try:
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                print(f"Error al buscar formulario (código {response.status_code}): {response.text}")
                return None, None

            data = response.json()
            forms_data = data.get('forms', data) # A veces la respuesta no viene anidada
            
            forms_list = forms_data.get('formDataInstances', [])
            
            for form in forms_list:
                if form.get('number') == numero:
                    # ¡Encontrado! Retorna el ID y el objeto
                    return form.get('id'), form
            
            # Lógica de paginación
            next_link_data = forms_data.get('_links', {}).get('next')
            if not next_link_data:
                break # No hay más páginas
            
            # Extraer el 'continuationToken' para la siguiente página
            next_href = next_link_data.get('href', '')
            if 'continuationToken=' in next_href:
                params['continuationToken'] = next_href.split('continuationToken=')[-1]
                params.pop('$top', None) # Ya no es necesario
            else:
                break # No se pudo encontrar el token de paginación
                
        except Exception as e:
            print(f"Excepción al buscar formulario: {str(e)}")
            return None, None

    # Si sale del bucle sin encontrarlo
    print(f"No se encontró ningún formulario con el número: {numero}")
    return None, None

@app.route('/formulario-synchro')
def formulario_synchro():
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
    # Asumiendo que has guardado el archivo como 'indexFormulario.html' en tu carpeta 'templates'
    return render_template('indexFormulario.html')

@app.route('/get-synchro-form-data')
def get_synchro_data():
    form_number = request.args.get('form_number')
    if not form_number:
        return jsonify({'error': 'Falta form_number'}), 400

    token = obtener_token() # (Tu función de Synchro)
    if not token:
        return jsonify({'error': 'No se pudo obtener el token'}), 500

    form_id, form_data = obtener_id_por_numero(token, form_number) # (Tu función de Synchro)

    if not form_id:
        return jsonify({'error': 'Formulario no encontrado'}), 404

    # Devuelve el 'number' y las 'properties'
    return jsonify({
        'id': form_id,
        'number': form_data.get('number'),
        'properties': form_data.get('properties', {})
    })

@app.route('/update-synchro-form', methods=['POST'])
def update_synchro_data():
    try:
        data = request.json
        form_number = data.get('form_number')
        new_properties = data.get('properties')
        # Aquí también puedes manejar data.get('media')

        if not form_number or not new_properties:
            return jsonify({'error': 'Faltan datos (form_number, properties)'}), 400

        token = obtener_token()
        if not token:
            return jsonify({'error': 'No se pudo obtener el token'}), 500

        form_id, form = obtener_id_por_numero(token, form_number)
        if not form_id:
            return jsonify({'error': 'Formulario no encontrado'}), 404

        props_actuales = form.get('properties', {})
        
        for section_name, new_items in new_properties.items():
            if not new_items: 
                continue

            lista_actual = props_actuales.get(section_name, [])
            
            # Generar UUIDs para los nuevos items ANTES de agregarlos
            for item in new_items:
                item['id'] = str(uuid.uuid4()) # Aseguramos un ID único
            
            lista_actual.extend(new_items)
            props_actuales[section_name] = lista_actual

        # --- PREPARAR Y ENVIAR EL PATCH ---
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.bentley.itwin-platform.v2+json',
            'Content-Type': 'application/json'
        }
        
        cambios = {
            'properties': props_actuales
        }
        
        # --- CAMBIO 1: Usar SYNCHRO_CONFIG en lugar de BASE_URL ---
        url = f"{SYNCHRO_CONFIG['forms_url']}/{form_id}"
        
        # --- CAMBIO 2: Usar requests.patch (con 's') ---
        response = requests.patch(url, headers=headers, json=cambios)
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            print(f"Error al actualizar Synchro ({response.status_code}): {response.text}")
            return jsonify({'error': 'Error al actualizar Synchro', 'details': response.text}), response.status_code

    except Exception as e:
        print(f"Excepción en update_synchro_data: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error interno del servidor: {str(e)}'}), 500


@app.route('/get-synchro-project-data')
def get_synchro_project_data():
    return jsonify({
        'codigo_proyecto':    'CO-CARR',
        'contrato':           '4500042183',
        'contratista':        'J.E. JAIMES INGENIEROS S.A.',
        'form_definition_id': SYNCHRO_FORM_DEFINITION_ID
    })

@app.route('/get-form-definitions')
def get_form_definitions():
    token = obtener_token()
    if not token:
        return jsonify({'error': 'No se pudo obtener token'}), 500
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.bentley.itwin-platform.v2+json'
    }
    
    response = requests.get(
        'https://api.bentley.com/forms',
        headers=headers,
        params={
            'iTwinId': SYNCHRO_CONFIG['itwin_id'],
            '$top': 50
        }
    )
    
    return jsonify({
        'status': response.status_code,
        'body': response.json()
    })


@app.route('/cambiar-password', methods=['GET'])
def cambiar_password_page():
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
    return render_template('cambiarPassword.html')

@app.route('/cambiar-password', methods=['POST'])
def cambiar_password():
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data             = request.get_json()
    password_nuevo   = data.get('password_nuevo')
    password_confirm = data.get('password_confirm')

    if password_nuevo != password_confirm:
        return jsonify({'error': 'Las contraseñas no coinciden'}), 400

    if len(password_nuevo) < 8:
        return jsonify({'error': 'La contraseña debe tener al menos 8 caracteres'}), 400

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                UPDATE usuario 
                SET password = %s, estado = 'activo'
                WHERE user_id = %s
            """, (generate_password_hash(password_nuevo), session['user_id']))

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error cambiando contraseña: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/get-form-detail')
def get_form_detail():
    token = obtener_token()
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.bentley.itwin-platform.v2+json'
    }
    response = requests.get(
        f"https://api.bentley.com/forms/e4bQKVghekuuA8Y6dmHKWFDXLUqEPIpFt_QjKefA5yk",
        headers=headers
    )
    forms = response.json().get('forms', {}).get('formDataInstances', [])
    
    # Buscar uno que sea del tipo 2.02
    for form in forms:
        if '2.02' in form.get('number', '') or 'Calidad' in form.get('type', ''):
            # Obtener detalle completo de ese formulario
            detail = requests.get(
                f"https://api.bentley.com/forms/{form['id']}",
                headers=headers
            )
            return jsonify({'status': detail.status_code, 'body': detail.json()})
    
    return jsonify(response.json())

@app.route('/create-synchro-form', methods=['POST'])
def create_synchro_form():
    try:
        data               = request.json
        form_definition_id = data.get('form_definition_id') or SYNCHRO_FORM_DEFINITION_ID
        new_properties     = data.get('properties', {})
        token = obtener_token()   # reutiliza la función que ya existe
        if not token:
            return jsonify({'error': 'No se pudo obtener token'}), 500
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept':        'application/vnd.bentley.itwin-platform.v2+json',
            'Content-Type':  'application/json',
            'Prefer':        'return=representation'
        }
        body = {
            'formId': form_definition_id,
            'properties': new_properties
        }
        response = requests.post(SYNCHRO_CONFIG['forms_url'], headers=headers, json=body, timeout=15)
        if response.status_code in (200, 201):
            created = response.json().get('form', response.json())
            return jsonify({'success': True, 'form_id': created.get('id'), 'number': created.get('number')}), 201
        return jsonify({'error': response.text}), response.status_code
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/formulario')
def indexFormulario():
    """Muestra el formulario con datos pre-cargados"""
    proyecto = {
        'codigo': '10111',
        'contratista': 'ABCD',
        'contrato': '001'
    }
    return render_template('indexFormulario.html', proyecto=proyecto)

@app.route('/registros')
def registros():
    print(f"empresa_id en sesión: {session.get('empresa_id')}")
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
    
    # Obtener proyectos de PostgreSQL
    db_projects = get_user_projects(session['user_id'])
    
    # Obtener proyectos de Azure Blob (si aún los necesitas)
    #blob_projects = get_projects_from_blob()  # Tu función existente
    
    # Combinar proyectos (o usar solo los de PostgreSQL)
    return render_template('registros.html', 
                         db_projects=db_projects)

# Ruta para la vista "history"
@app.route('/history')
def history():
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
    
    # Obtenemos los proyectos de PostgreSQL
    db_projects = get_user_projects(session['user_id'])
    
    # IMPORTANTE: get_user_projects ya devuelve 'name', 
    # pero asegúrate de que el HTML lo use correctamente.
    return render_template('history.html', db_projects=db_projects)

@app.route('/configuracion')
def configuracion():
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
    if session.get('user_rol') != 'admin':
        return redirect(url_for('registros'))
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # Usuarios del mismo tenant/organización
        cursor.execute("""
            SELECT user_id, name, apellido, email, rol, estado
            FROM usuario
            WHERE empresa_id = %s
            ORDER BY name ASC
        """, (session.get('empresa_id'),))
        
        miembros = []
        for row in cursor.fetchall():
            miembros.append({
                'user_id': row[0], 'nombre': row[1], 'apellido': row[2] or '',
                'email': row[3], 'rol': row[4] or 'Sin rol',
                'estado': row[5] or 'pendiente', 'foto': None
            })
        
        cursor.execute("""
            SELECT url FROM empresa_logos
            WHERE empresa_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (session.get('empresa_id'),))
        logo_row = cursor.fetchone()
        logo_actual = logo_row[0] if logo_row else None

        return render_template('configuracion.html', miembros=miembros, logo_actual=logo_actual)
        #return render_template('configuracion.html', miembros=miembros)
    except Exception as e:
        print(f"Error en usuario: {e}")
        return render_template('configuracion.html', miembros=[])
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

@app.route('/inventario')
def inventario():
    return render_template('inventario.html')

# En tu archivo app.py

@app.route('/historialRegistro/<int:id_proyecto>')
def historialregistro(id_proyecto):
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
    
    #conn = None
    try:
        #conn, cursor = get_db_connection()
        with db_connection() as (conn, cursor):

            # 1. Info del proyecto (Usar comillas dobles para la tabla)
            cursor.execute('SELECT nombre_proyecto, cliente FROM proyectos WHERE id = %s', (id_proyecto,))
            proyecto_info = cursor.fetchone()

            if not proyecto_info:
                return redirect(url_for('history'))

            # 2. Consultar registros de la nueva tabla Terranovus
            cursor.execute("""
                SELECT c.id, c.nombre, c.empresa, c.cargo,
                    c.telefono, c.email, c.ciudad, c.notas,
                    c.created_at,
                    u.name, u.apellido, u.cargo as user_cargo
                FROM contactos c
                LEFT JOIN usuario u ON u.user_id = c.user_id
                WHERE c.id_proyecto = %s
                ORDER BY c.created_at DESC
            """, (id_proyecto,))

            registros_rows = cursor.fetchall()
            reportes_completos = []

            for r_row in registros_rows:
                id_c, nombre, empresa, cargo, telefono, email, ciudad, notas, created_at, u_name, u_apellido, u_cargo = r_row

                u_name    = u_name or ''
                u_apellido = u_apellido or ''
                iniciales = (u_name[0] + u_apellido[0]).upper() if u_name and u_apellido else '??'

                if created_at:
                    colombia_tz    = pytz.timezone('America/Bogota')
                    created_at_col = created_at.replace(tzinfo=timezone.utc).astimezone(colombia_tz)
                    hora           = created_at_col.strftime('%I:%M %p')
                    fecha_str      = created_at_col.strftime('%d/%m/%Y')
                else:
                    hora      = None
                    fecha_str = 'S/F'

                # Fotos del contacto
                cursor.execute("""
                    SELECT imagen_url, descripcion
                    FROM contacto_imagenes
                    WHERE contacto_id = %s
                """, (id_c,))

                fotos = []
                for f in cursor.fetchall():
                    if f[0]:
                        fotos.append({'url': f[0], 'base64': None, 'desc': f[1] or ''})

                reportes_completos.append({
                    'id_registro':       id_c,
                    'actividad':         nombre,
                    'descripcion':       f"{empresa or ''} · {cargo or ''}".strip(' ·'),
                    'estado':            ciudad or '',
                    'avance':            None,
                    'fecha': fecha_str,
                    'hora':  hora,
                    #'fecha':             created_at.strftime('%d/%m/%Y') if created_at else 'S/F',
                    #'hora':              created_at.strftime('%I:%M %p') if created_at else None,
                    'usuario_nombre':    f"{u_name} {u_apellido}".strip() or 'Sin asignar',
                    'usuario_cargo':     u_cargo or '',
                    'usuario_iniciales': iniciales,
                    'fotos':             fotos,
                    'telefono':          telefono or '',
                    'email':             email or '',
                    'notas':             notas or ''
                })


            '''
            cursor.execute("""
                SELECT r.id, r.fecha, r.actividad, r.descripcion_actividad, 
                    r.estado, r.porcentaje_avance,
                    u.name, u.apellido, u.cargo
                FROM registros r
                LEFT JOIN usuario u ON u.user_id = r.user_id
                WHERE r.id_proyecto = %s 
                ORDER BY r.fecha DESC, r.id DESC
            """, (id_proyecto,))
            
            registros_rows = cursor.fetchall()
            reportes_completos = []

            for r_row in registros_rows:
                id_reg, fecha_dt, act, desc, est, avan, nombre, apellido, cargo = r_row
                
                # 3. Consultar fotos asociadas a este registro
                cursor.execute("""
                    SELECT imagen_url, description 
                    FROM fotos_registro
                    WHERE id_registro = %s
                """, (id_reg,))
                fotos_raw = cursor.fetchall()

                fotos = []
                for f in fotos_raw:
                    url  = f[0]
                    desc = f[1] or ''
                    if url:
                        fotos.append({'url': url, 'base64': None, 'desc': desc})
                    #elif b64:
                        #img_str = b64.split(',')[1] if ',' in b64 else b64
                        #fotos.append({'url': None, 'base64': img_str, 'desc': desc})

                reportes_completos.append({
                    'id_registro':       id_reg,
                    'fecha':             fecha_dt.strftime('%d/%m/%Y') if fecha_dt else "S/F",
                    'actividad':         act,
                    'descripcion':       desc,
                    'estado':            est,
                    'avance':            avan,
                    'usuario_nombre':    f"{nombre or ''} {apellido or ''}".strip() or 'Sin asignar',
                    'usuario_cargo':     cargo or '',
                    'usuario_iniciales': ((nombre or '')[0] + (apellido or '')[0]).upper() if nombre and apellido else '??',
                    'fotos':             fotos
                })
                '''

            return render_template('historialRegistro.html', 
                                proyecto=proyecto_info, 
                                reportes=reportes_completos, 
                                id_proyecto=id_proyecto)
    except Exception as e:
        print(f"Error en historialregistro: {e}")
        return redirect(url_for('history'))


@app.route('/guardar_contacto', methods=['POST'])
def guardar_contacto():
    if 'user_id' not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        data       = request.json
        empresa_id = session.get('empresa_id')

        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO contactos 
                    (empresa_id, user_id, id_proyecto, nombre, empresa, cargo, 
                    telefono, email, ciudad, notas)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                empresa_id, session['user_id'],
                data.get('id_proyecto'),
                data.get('nombre'), data.get('empresa'), data.get('cargo'),
                data.get('telefono'), data.get('email'),
                data.get('ciudad'), data.get('notas')
            ))

            contacto_id = cursor.fetchone()[0]

            for img in data.get('imagenes', []):
                cursor.execute("""
                    INSERT INTO contacto_imagenes 
                        (contacto_id, empresa_id, imagen_url, descripcion)
                    VALUES (%s, %s, %s, %s)
                """, (contacto_id, empresa_id, img.get('url'), img.get('descripcion')))

        return jsonify({"status": "success", "message": "Contacto guardado"}), 201

    except Exception as e:
        print(f"Error guardando contacto: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/detalleContacto/<int:id_contacto>')
def detalleContacto(id_contacto):
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
 
    conn = None
    try:
        #conn, cursor = get_db_connection()
        with db_connection() as (conn, cursor):

            cursor.execute("""
                SELECT 
                    c.id, c.nombre, c.empresa, c.cargo,
                    c.telefono, c.email, c.ciudad, c.notas,
                    c.created_at,
                    u.name, u.apellido, u.cargo as user_cargo
                FROM contactos c
                LEFT JOIN usuario u ON u.user_id = c.user_id
                WHERE c.id = %s
            """, (id_contacto,))
    
            row = cursor.fetchone()
            if not row:
                return redirect(url_for('registros'))
    
            created_at = row[8]
            #temporal
            print(f"created_at raw: {created_at}")
            print(f"created_at tzinfo: {created_at.tzinfo if created_at else 'None'}")
            nombre     = row[9] or ''
            apellido   = row[10] or ''
            iniciales  = (nombre[0] + apellido[0]).upper() if nombre and apellido else '??'

            # Convertir UTC a Colombia
            if created_at:
                colombia_tz    = pytz.timezone('America/Bogota')
                created_at_col = created_at.replace(tzinfo=timezone.utc).astimezone(colombia_tz)
                fecha_str      = created_at_col.strftime('%d %b %Y')
                created_texto  = created_at_col.strftime('%d %b %Y - %H:%M')
            else:
                fecha_str     = ''
                created_texto = ''
    
            contacto = {
                'id':                id_contacto,
                'nombre':            row[1],
                'empresa':           row[2] or '',
                'cargo':             row[3] or '',
                'telefono':          row[4] or '',
                'email':             row[5] or '',
                'ciudad':            row[6] or '',
                'notas':             row[7] or '',
                'fecha':            fecha_str,
                'created_at_texto': created_texto,
                #'fecha':             created_at.strftime('%d %b %Y') if created_at else '',
                #'created_at_texto':  created_at.strftime('%d %b %Y - %H:%M') if created_at else '',
                'usuario_nombre':    f"{nombre} {apellido}".strip() or 'Sin asignar',
                'usuario_cargo':     row[11] or '',
                'usuario_iniciales': iniciales,
                'imagenes':          []
            }
    
            cursor.execute("""
                SELECT imagen_url, descripcion
                FROM contacto_imagenes
                WHERE contacto_id = %s
            """, (id_contacto,))
    
            for img_row in cursor.fetchall():
                if img_row[0]:
                    contacto['imagenes'].append({
                        'url':  img_row[0],
                        'desc': img_row[1] or ''
                    })
    
            return render_template('detalleContacto.html', contacto=contacto)
 
    except Exception as e:
        print(f"Error en detalleContacto: {e}")
        return redirect(url_for('registros'))


@app.route('/formContacto')
def form_contacto():
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
    return render_template('formContacto.html')


@app.route('/detalleRegistro/<int:id_registro>')
def detalleRegistro(id_registro):
    if 'user_id' not in session:
        return redirect(url_for('principalscreen'))
 
    conn = None
    try:
        conn, cursor = get_db_connection()
 
        # Traer el registro con datos del usuario
        cursor.execute("""
            SELECT 
                r.id,
                r.actividad,
                r.descripcion_actividad,
                r.estado,
                r.porcentaje_avance,
                r.fecha,
                r.created_at,
                u.name,
                u.apellido,
                u.cargo
            FROM registros r
            LEFT JOIN usuario u ON u.user_id = r.user_id
            WHERE r.id = %s
        """, (id_registro,))
 
        row = cursor.fetchone()
 
        if not row:
            return redirect(url_for('registros'))
 
        # Extraer hora de created_at
        created_at = row[6]
        #hora = created_at.strftime('%I:%M %p') if created_at else None
        
        if created_at:
            colombia_tz  = pytz.timezone('America/Bogota')
            created_at_col = created_at.replace(tzinfo=timezone.utc).astimezone(colombia_tz)
            hora = created_at_col.strftime('%I:%M %p')
        else:
            hora = None
        created_at_texto = created_at.strftime('%d %b %Y - %H:%M') if created_at else None
 
        # Iniciales del usuario
        nombre  = row[7] or ''
        apellido = row[8] or ''
        iniciales = (nombre[0] + apellido[0]).upper() if nombre and apellido else '??'
 
        registro = {
            'id_registro':      row[0],
            'actividad':        row[1],
            'descripcion':      row[2],
            'estado':           row[3] or 'Sin estado',
            'avance':           row[4] or 0,
            'fecha':            row[5].strftime('%d %b %Y') if row[5] else '',
            'hora':             hora,
            'created_at_texto': created_at_texto,
            'usuario_nombre':   f"{nombre} {apellido}".strip() or 'Sin asignar',
            'usuario_cargo':    row[9] or '',
            'usuario_iniciales': iniciales,
            'fotos':            []
        }
 
        # Traer fotos del registro
        cursor.execute("""
            SELECT imagen_url, description
            FROM fotos_registro
            WHERE id_registro = %s
        """, (id_registro,))

        for foto_row in cursor.fetchall():
            url  = foto_row[0]
            desc = foto_row[1] or ''
            if url:
                registro['fotos'].append({'url': url, 'desc': desc})

        return render_template('detalleRegistro.html', registro=registro)
 
    except Exception as e:
        print(f"Error en detalleRegistro: {e}")
        return redirect(url_for('registros'))
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

@app.route('/disciplinerecords')
def disciplinerecords():
    return render_template('disciplinerecords.html')

@app.route('/projectdetails')
def projectdetails():
    return render_template('projectdetails.html')

import json

@app.route('/guardar_reporte_terranovus', methods=['POST'])
def guardar_reporte_terranovus():
    data = request.json
    conn = None
    try:
        conn, cursor = get_db_connection()

        # Extraer el ID del proyecto y del usuario
        id_proyecto = data.get('id_proyecto')
        user_id = session.get('user_id')

        # Recorrer cada actividad enviada desde el frontend
        for nota in data.get('notas', []):
            # 1. Insertar en la tabla maestra de registros
            cursor.execute("""
                INSERT INTO registros(
                    id_proyecto, actividad, descripcion_actividad, 
                    estado, porcentaje_avance, user_id, empresa_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s) 
                RETURNING id
            """, (
                id_proyecto, 
                nota.get('titulo'), # Campo 'Actividad' en tu SQL
                nota.get('texto'),  # Campo 'descripcion_actividad'
                nota.get('estado'), 
                nota.get('avance'), 
                user_id,
                session.get('empresa_id')
            ))
            
            id_registro = cursor.fetchone()[0]

            # 2. Insertar las fotos asociadas a ESTA actividad específica
            for foto_obj in nota.get('fotos_detalle', []):
                cursor.execute("""
                    INSERT INTO fotos_registro (
                        id_registro, imagen_url, description, empresa_id
                    ) VALUES (%s, %s, %s, %s)
                """, (
                    id_registro,
                    foto_obj.get('imagen_url'),
                    foto_obj.get('description'),
                    session.get('empresa_id')
                ))

        conn.commit()
        return jsonify({"status": "success", "message": "Reporte guardado correctamente"}), 201

    except Exception as e:
        if conn: conn.rollback()
        print(f"Error al guardar: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

@app.route('/add_project', methods=['GET', 'POST'])
def add_project():
    if 'user_id' not in session:
        return jsonify({"error": "No autorizado"}), 401

    if request.method == 'POST':
        try:
            data       = request.json
            empresa_id = session.get('empresa_id', 1)

            with db_connection() as (conn, cursor):
                cursor.execute("""
                    INSERT INTO proyectos (
                        nombre_proyecto, fecha_inicio, fecha_fin, cliente, contratista, 
                        orden_de_trabajo, ubicacion, user_id, empresa_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) 
                    RETURNING id
                """, (
                    data.get('project-name'), data.get('start-date'), data.get('end-date'),
                    data.get('cliente'), data.get('contratista'), data.get('orden-trabajo'),
                    data.get('location'), session['user_id'], empresa_id
                ))

                nuevo_id = cursor.fetchone()[0]

                miembros = data.get('miembros', [])
                if not miembros:
                    miembros = [session['user_id']]

                for uid in miembros:
                    cursor.execute("""
                        INSERT INTO proyecto_usuarios (id_proyecto, user_id, empresa_id)
                        VALUES (%s, %s, %s)
                    """, (nuevo_id, uid, empresa_id))

            return jsonify({"status": "success", "message": "Proyecto registrado exitosamente"}), 201

        except Exception as e:
            print(f"Error en BD: {str(e)}")
            return jsonify({"status": "error", "error": str(e)}), 500

    # GET
    usuarios = []
    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT user_id, name, apellido, cargo 
                FROM usuario 
                WHERE estado = 'activo'
                AND empresa_id = %s
                ORDER BY name ASC
            """, (session.get('empresa_id'),))
            for row in cursor.fetchall():
                usuarios.append({
                    'user_id':  row[0],
                    'name':     row[1],
                    'apellido': row[2],
                    'cargo':    row[3] or 'Sin cargo'
                })
    except Exception as e:
        print(f"Error al cargar usuarios: {e}")

    return render_template('addproject.html', usuarios=usuarios)


#@app.route('/generar-hash')
#def generar_hash():
    #from werkzeug.security import generate_password_hash
    #return generate_password_hash('Bitacora2026*')


@app.route('/ask', methods=['POST'])
def ask_question_route():
    data = request.json
    question = data.get('question', '')
    if not question:
        return jsonify({'error': 'No question provided'}), 400

    success = synthesize_speech(question)
    if success:
        return jsonify({'response': ''}), 200
    else:
        return jsonify({'error': 'Error al sintetizar la pregunta.'}), 500


@app.route('/guardar-inspeccion', methods=['POST'])
def guardar_inspeccion():
    try:
        data = request.json
        project_id = data.get('project_id')
        items = data.get('items', [])

        if not project_id or not items:
            return jsonify({'success': False, 'error': 'Datos incompletos'}), 400

        conn, cursor = get_db_connection()

        for item in items:
            cursor.execute("""
                INSERT INTO reporte_fiscalizacion (
                    id_proyecto, 
                    edificacion_zona, 
                    item_numero, 
                    area_inspeccionada, 
                    especificacion_tecnica, 
                    condicion_observada, 
                    cumple, 
                    observaciones, 
                    acciones_correctivas
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                project_id,
                item['edificacion_zona'],
                item['item_numero'],
                item['area_inspeccionada'],
                item['especificacion_tecnica'],
                item['condicion_observada'],
                item['cumple'],
                item['observaciones'],
                item['acciones_correctivas']
            ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'mensaje': 'Inspección guardada correctamente'})
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/guardar-registro', methods=['POST'])
def guardar_registro():
    conn = None
    try:
        data = request.get_json()
        project_id = data.get('project_id')
        items = data.get('items', [])
        # Nota: 'fotos' y 'videos' ahora deberían venir dentro de cada objeto en 'items'
        
        if not project_id or not items:
            return jsonify({"error": "Faltan datos requeridos."}), 400

        conn, cursor = get_db_connection()

        # 1. Bucle principal para guardar cada ítem
        for item in items:
            cursor.execute("""
                INSERT INTO reporte_fiscalizacion (
                    id_proyecto, edificacion_zona, item_numero, area_inspeccionada, 
                    especificacion_tecnica, condicion_observada, cumple, 
                    observaciones, acciones_correctivas
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_reporte
            """, (
                project_id, item.get('edificacion_zona'), item.get('item_numero'),
                item.get('area_inspeccionada'), item.get('especificacion_tecnica'),
                item.get('condicion_observada'), item.get('cumple'),
                item.get('observaciones'), item.get('acciones_correctivas')
            ))
            
            # Capturamos el ID específico de ESTE ítem recién insertado
            id_item_actual = cursor.fetchone()[0]

            # 2. GUARDAR FOTOS ESPECÍFICAS DE ESTE ÍTEM (NUEVA UBICACIÓN)
            # El frontend ahora debe enviar las fotos dentro de cada item
            fotos_item = item.get('fotos', []) 
            for foto_obj in fotos_item:
                cursor.execute("""
                    INSERT INTO fotos_registro (id_registro, imagen_base64, description) 
                    VALUES (%s, %s, %s)
                """, (id_item_actual, foto_obj.get('file_data'), foto_obj.get('description')))

            # 3. GUARDAR VIDEOS ESPECÍFICOS DE ESTE ÍTEM
            videos_item = item.get('videos', [])
            for video_obj in videos_item:
                cursor.execute("""
                    INSERT INTO videos_registro (id_registro, video_base64, description) 
                    VALUES (%s, %s, %s)
                """, (id_item_actual, video_obj.get('file_data'), video_obj.get('description')))

        conn.commit()
        return jsonify({"mensaje": "¡Reporte guardado!"}), 200

    except Exception as e:
        if conn: conn.rollback()
        print(f"Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

@app.route('/eliminar-usuario/<int:user_id>', methods=['DELETE'])
def eliminar_usuario(user_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'No autenticado'}), 401

    # Evitar que el admin se elimine a sí mismo
    if user_id == session['user_id']:
        return jsonify({'success': False, 'error': 'No puedes eliminarte a ti mismo'})

    conn = None
    try:
        conn, cursor = get_db_connection()

        # Verificar que el usuario pertenece a la misma empresa
        cursor.execute("""
            SELECT user_id FROM usuario
            WHERE user_id = %s
            AND empresa_id = %s
        """, (user_id, session.get('empresa_id')))

        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'Usuario no encontrado en tu organización'})

        cursor.execute("DELETE FROM usuario WHERE user_id = %s", (user_id,))
        conn.commit()
        return jsonify({'success': True})

    except Exception as e:
        print(f"Error eliminando usuario: {e}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)


@app.route('/subir-logo', methods=['POST'])
def subir_logo():
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json()
    file_data = data.get('imagen')
    empresa_id = session.get('empresa_id')

    try:
        if ',' in file_data:
            header, b64 = file_data.split(',', 1)
            ext = 'png' if 'png' in header else 'jpg'
        else:
            b64, ext = file_data, 'jpg'

        imagen_bytes = base64.b64decode(b64)
        nombre_archivo = f"{uuid.uuid4()}.{ext}"
        ruta = f"logos/{empresa_id}/{nombre_archivo}"

        supabase_client.storage.from_('fotos-bitacora').upload(
            ruta,
            imagen_bytes,
            {"content-type": f"image/{ext}"}
        )

        url_publica = f"{SUPABASE_URL}/storage/v1/object/public/fotos-bitacora/{ruta}"

        # Guardar en BD
        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO empresa_logos (empresa_id, url, creado_por)
                VALUES (%s, %s, %s)
            """, (empresa_id, url_publica, session['user_id']))

        return jsonify({'url': url_publica}), 200

    except Exception as e:
        print(f"Error subiendo logo: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/logos-empresa', methods=['GET'])
def logos_empresa():
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401
    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT id, url, created_at
                FROM empresa_logos
                WHERE empresa_id = %s
                ORDER BY created_at DESC
            """, (session.get('empresa_id'),))
            logos = [{'id': r[0], 'url': r[1], 'fecha': r[2].strftime('%d/%m/%Y') if r[2] else ''} 
                     for r in cursor.fetchall()]
        return jsonify({'logos': logos}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500 


@app.route('/eliminar-proyecto', methods=['POST'])
def eliminar_proyecto():
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json()
    proyecto_id = data.get('id_proyecto')

    if not proyecto_id:
        return jsonify({'error': 'Falta el ID del proyecto'}), 400

    try:
        conn, cursor = get_db_connection()

        # Asegurarse de que el proyecto pertenece al usuario
        cursor.execute("""
            DELETE FROM proyectos
            WHERE id_proyecto = %s AND user_id = %s
        """, (proyecto_id, session['user_id']))
        conn.commit()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

@app.route('/transcribe-audio', methods=['POST'])
def transcribe_audio():
    try:
        if 'audio' not in request.files:
            print("🔴 No se recibió archivo de audio.")
            return jsonify({"error": "No se envió el archivo de audio"}), 400

        file = request.files['audio']
        print(f"📥 Recibido archivo: {file.filename}")

        # Guardar el archivo temporalmente
        temp_input = tempfile.NamedTemporaryFile(delete=False, suffix=".webm")
        file.save(temp_input.name)
        print(f"💾 Guardado en: {temp_input.name}")

        temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        formato_detectado = None

        try:
            print("🔍 Intentando decodificar como webm...")
            audio = AudioSegment.from_file(temp_input.name, format="webm")
            print("✅ Decodificado como webm.")
            formato_detectado = "webm"
        except Exception as e_webm:
            print("⚠️ Falla al decodificar como webm:", str(e_webm))
            try:
                print("🔁 Intentando decodificar como mp4...")
                audio = AudioSegment.from_file(temp_input.name, format="mp4")
                print("✅ Decodificado como mp4.")
                formato_detectado = "mp4"
            except Exception as e_mp4:
                print("❌ Fallo total al decodificar audio.")
                traceback.print_exc()
                return jsonify({
                    "error": "No se pudo procesar el audio.",
                    "error_webm": str(e_webm),
                    "error_mp4": str(e_mp4)
                }), 500

        # Exportar a WAV
        audio.export(temp_wav.name, format="wav")
        print("🔄 Exportado a WAV:", temp_wav.name)

        # Transcribir con Azure
        speech_config = get_speech_config()
        audio_config = speechsdk.audio.AudioConfig(filename=temp_wav.name)
        recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
        result = recognizer.recognize_once_async().get()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            print("✅ Texto reconocido:", result.text)
            return jsonify({
                "text": result.text,
                "formato_detectado": formato_detectado
            })
        else:
            print("⚠️ No se reconoció el audio:", result.reason)
            return jsonify({
                "error": "No se reconoció el audio.",
                "formato_detectado": formato_detectado
            }), 400

    except Exception as e:
        print("❌ Error general en transcribe_audio:", str(e))
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

#Exportar registros seleccionados a Excel
@app.route('/exportar-registros-excel', methods=['POST'])
def exportar_registros_excel():
    registro_ids = request.form.getlist('registro_ids')
    project_id = request.form.get('project_id')

    if not registro_ids and not project_id:
        return "No se seleccionaron registros ni proyecto", 400

    try:
        conn, cursor = get_db_connection()

        if not registro_ids:
            cursor.execute("""
                SELECT id_registro, zona_intervencion, items, metros_lineales, proximas_tareas, foto_base64
                FROM registrosbitacoraeqing
                WHERE id_proyecto = %s
                ORDER BY id_registro DESC
            """, (project_id,))
        else:
            format_ids = tuple(map(int, registro_ids))
            cursor.execute("""
                SELECT id_registro, zona_intervencion, items, metros_lineales, proximas_tareas, foto_base64
                FROM registrosbitacoraeqing
                WHERE id_registro IN %s
                ORDER BY id_registro DESC
            """, (format_ids,))

        rows = cursor.fetchall()

        wb = Workbook()
        ws = wb.active
        ws.title = "Registros"

        # Encabezado
        ws.append(["ID", "Zona de Intervención", "Ítems", "Metros Lineales", "Próximas Tareas", "Foto"])

        row_index = 2  # Comienza después del encabezado

        for row in rows:
            id_registro, zona, items, metros, tareas, foto_base64 = row
            ws.append([id_registro, zona, items, metros, tareas, ""])  # celda para imagen

            if foto_base64:
                try:
                    header, base64_data = foto_base64.split(',', 1) if ',' in foto_base64 else ('', foto_base64)
                    image_data = base64.b64decode(base64_data)
                    img = Image.open(io.BytesIO(image_data))
                    img.thumbnail((120, 120))  # redimensiona para celda
                    image_io = io.BytesIO()
                    img.save(image_io, format='PNG')
                    image_io.seek(0)

                    img_excel = ExcelImage(image_io)
                    img_excel.anchor = f"F{row_index}"
                    ws.add_image(img_excel)

                    # Ajustar altura de fila
                    ws.row_dimensions[row_index].height = 90
                except Exception as img_err:
                    print(f"Error al procesar imagen para registro {id_registro}: {img_err}")

            row_index += 1

        # Ajuste de anchos de columnas
        ws.column_dimensions['A'].width = 12  # ID
        ws.column_dimensions['B'].width = 30  # Zona de intervención
        ws.column_dimensions['C'].width = 25  # Ítems
        ws.column_dimensions['D'].width = 20  # Metros lineales
        ws.column_dimensions['E'].width = 35  # Próximas tareas
        ws.column_dimensions['F'].width = 18  # Imagen

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        return send_file(output,
                         download_name="registros_bitacora.xlsx",
                         as_attachment=True,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception as e:
        print(f"Error al exportar: {e}")
        return "Error al exportar", 500
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

@app.route('/exportar-proyectos-pdf', methods=['POST'])
def exportar_proyectos_pdf():
    project_ids = request.form.getlist('project_ids')
    if not project_ids:
        return "No se seleccionaron proyectos", 400

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    try:
        conn, cursor = get_db_connection()

        for pid in project_ids:
            # 1. Info del proyecto (Tabla: proyectos)
            cursor.execute("""
                SELECT nombre_proyecto, cliente, contratista, orden_de_trabajo, ubicacion, fecha_inicio 
                FROM proyectos WHERE id_proyecto = %s
            """, (pid,))
            proyecto = cursor.fetchone()
            if not proyecto: continue

            nombre, cliente, contratista, ot, ubicacion, f_inicio = proyecto
            pdf.add_page()
            
            # --- ENCABEZADO TÉCNICO (190mm Total) ---
            y_inicial = pdf.get_y()
            
            # Celda Logo (40mm)
            pdf.rect(10, y_inicial, 40, 20)
            logo_path = os.path.join('static', 'logo.png')
            if os.path.exists(logo_path):
                pdf.image(logo_path, x=15, y=y_inicial + 2, w=30)
            
            # Celda Título (150mm)
            pdf.set_xy(50, y_inicial)
            pdf.set_font("Arial", 'B', 14)
            pdf.cell(150, 20, "REPORTE TÉCNICO DE ACTIVIDADES", border=1, ln=True, align='C')

            # Filas de Información (Ancho total 190mm)
            pdf.set_font("Arial", 'B', 9)
            pdf.cell(30, 8, "PROYECTO:", border=1)
            pdf.set_font("Arial", '', 9)
            pdf.cell(65, 8, f"{nombre or ''}", border=1)
            pdf.set_font("Arial", 'B', 9)
            pdf.cell(40, 8, "CLIENTE:", border=1)
            pdf.set_font("Arial", '', 9)
            pdf.cell(55, 8, f"{cliente or ''}", border=1, ln=True)

            pdf.set_font("Arial", 'B', 9)
            pdf.cell(30, 8, "UBICACIÓN:", border=1)
            pdf.set_font("Arial", '', 9)
            pdf.cell(65, 8, f"{ubicacion or ''}", border=1)
            pdf.set_font("Arial", 'B', 9)
            pdf.cell(40, 8, "ORDEN DE TRABAJO:", border=1)
            pdf.set_font("Arial", '', 9)
            pdf.cell(55, 8, f"{ot or ''}", border=1, ln=True)
            
            pdf.ln(10)

            # --- REGISTROS DE ACTIVIDAD ---
            cursor.execute("""
                SELECT id_registro, actividad, descripcion_actividad, estado, porcentaje_avance, fecha
                FROM registros
                WHERE id_proyecto = %s ORDER BY fecha DESC
            """, (pid,))
            registros = cursor.fetchall()

            for reg in registros:
                id_reg, actividad, desc, estado, avance, fecha_reg = reg
                
                # Encabezado Actividad
                pdf.set_fill_color(255, 240, 220)
                pdf.set_font("Arial", 'B', 11)
                pdf.cell(190, 8, f"FECHA: {fecha_reg} - ACTIVIDAD: {actividad or 'Sin actividad'}", ln=True, fill=True, border='T')
                
                # Descripción
                pdf.set_font("Arial", '', 10)
                pdf.multi_cell(190, 6, f"Descripción: {desc or 'Sin descripción'}", border='LR')

                # FIX: Forzar X al margen izquierdo después del multi_cell
                pdf.set_x(10)

                # ESTADO Y AVANCE (95mm + 95mm = 190mm)
                pdf.set_font("Arial", 'B', 10)
                pdf.set_fill_color(245, 245, 245)
                pdf.cell(95, 8, f" ESTADO: {estado or 'N/A'}", border=1, fill=True)
                pdf.cell(95, 8, f" AVANCE: {avance or 0}%", border=1, fill=True, ln=True)
                
                # --- SECCIÓN DE EVIDENCIA FOTOGRÁFICA ---
                cursor.execute("SELECT imagen_base64, description FROM fotos_registro WHERE id_registro = %s", (id_reg,))
                fotos = cursor.fetchall()
                
                if fotos:
                    pdf.ln(2)
                    pdf.set_font("Arial", 'B', 10)
                    pdf.cell(0, 8, "EVIDENCIA:", ln=True)
                    
                    img_w = 60
                    current_x = 10
                    
                    for i, (foto_data, foto_desc) in enumerate(fotos):
                        # Salto de página preventivo
                        if pdf.get_y() > 220:
                            pdf.add_page()
                            pdf.ln(5)
                            current_x = 10

                        # Fila de 3 fotos
                        if i > 0 and i % 3 == 0:
                            pdf.set_y(pdf.get_y() + 50)
                            current_x = 10

                        try:
                            header, encoded = foto_data.split(",", 1) if "," in foto_data else ("", foto_data)
                            img_bytes = base64.b64decode(encoded)
                            
                            with NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                                tmp.write(img_bytes)
                                tmp_path = tmp.name
                            
                            # Imagen
                            pdf.image(tmp_path, x=current_x, y=pdf.get_y(), w=img_w, h=40)
                            
                            # Descripción (Pie de foto opcional)
                            pdf.set_xy(current_x, pdf.get_y() + 41)
                            pdf.set_font("Arial", 'I', 7)
                            desc_txt = (foto_desc[:40]) if foto_desc else ""
                            pdf.cell(img_w, 4, desc_txt, align='C')
                            
                            current_x += img_w + 5
                            pdf.set_y(pdf.get_y() - 41)

                        except Exception as e:
                            print(f"Error procesando imagen: {e}")
                    
                    pdf.set_y(pdf.get_y() + 55)
                else:
                    pdf.ln(5)

        response_pdf = pdf.output(dest='S')
        return send_file(
            io.BytesIO(response_pdf),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"Reporte_Terranovus_{datetime.now().strftime('%Y%m%d')}.pdf"
        )

    except Exception as e:
        print(f"Error PDF: {e}")
        return f"Error: {str(e)}", 500
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)


@app.route('/tablero-bi')
def tablero_bi():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = None
    try:
        conn, cursor = get_db_connection()

        # 1. Estadísticas Generales de Proyectos
        cursor.execute('SELECT COUNT(*) FROM proyectos WHERE user_id = %s', (session['user_id'],))
        total_proyectos = cursor.fetchone()[0]

        # 2. Avance promedio y total de registros
        cursor.execute("""
            SELECT 
                COUNT(r.id_registro), 
                AVG(r.porcentaje_avance) 
            FROM registros r
            JOIN proyectos p ON r.id_proyecto = p.id_proyecto
            WHERE p.user_id = %s
        """, (session['user_id'],))
        stats = cursor.fetchone()
        total_registros = stats[0] or 0
        promedio_avance = round(stats[1], 2) if stats[1] else 0

        # 3. Conteo por Estados (para gráfico de torta)
        cursor.execute("""
            SELECT estado, COUNT(*) 
            FROM registros r
            JOIN proyectos p ON r.id_proyecto = p.id_proyecto
            WHERE p.user_id = %s
            GROUP BY estado
        """, (session['user_id'],))
        estados_raw = cursor.fetchall()
        
        # Convertimos a diccionario para fácil manejo en JS
        datos_estados = {row[0]: row[1] for row in estados_raw}

        return render_template('tableroBI.html', 
                               total_p=total_proyectos,
                               total_r=total_registros,
                               promedio=promedio_avance,
                               datos_estados=datos_estados)

    except Exception as e:
        print(f"Error en Tablero BI: {e}")
        return redirect(url_for('history'))
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)


@app.route('/exportar-proyectos-excel', methods=['POST'])
def exportar_proyectos_excel():
    project_ids = request.form.getlist('project_ids')
    
    if not project_ids:
        return "No se seleccionaron proyectos", 400

    try:
        conn, cursor = get_db_connection()
        wb = Workbook()
        wb.remove(wb.active)  # Eliminar hoja por defecto

        for pid in project_ids:
            try:
                pid_int = int(pid)
            except:
                continue

            # Obtener info del proyecto
            cursor.execute("""
                SELECT nombre_proyecto, fecha_inicio, fecha_fin, director_obra, ubicacion, coordenadas
                FROM proyectos WHERE id_proyecto = %s
            """, (pid_int,))
            proyecto = cursor.fetchone()
            if not proyecto:
                continue

            nombre, fecha_inicio, fecha_fin, director, ubicacion, coordenadas = proyecto
            sheet_title = (nombre[:30] or f"Proyecto {pid_int}").strip()
            ws = wb.create_sheet(title=sheet_title)

            # Encabezado de proyecto
            ws.append(["Nombre del Proyecto:", nombre])
            ws.append(["Fecha de Inicio:", str(fecha_inicio)])
            ws.append(["Fecha de Finalización:", str(fecha_fin)])
            ws.append(["Director del Proyecto:", director])
            ws.append(["Ubicación:", ubicacion])
            ws.append(["Coordenadas:", coordenadas])
            ws.append([])

            # Encabezado de registros
            ws.append(["ID", "Zona de Intervención", "Ítems Instalados", "Metros Lineales", "Próximas Tareas", "Foto"])

            # Obtener registros
            cursor.execute("""
                SELECT id_registro, zona_intervencion, items, metros_lineales, proximas_tareas, foto_base64
                FROM registrosbitacoraeqing
                WHERE id_proyecto = %s
                ORDER BY id_registro DESC
            """, (pid_int,))
            registros = cursor.fetchall()

            row_index = 9
            for registro in registros:
                idr, zona, items, metros, tareas, foto = registro
                ws.append([idr, zona, items, metros, tareas, ""])

                if foto:
                    try:
                        header, base64_data = foto.split(',', 1) if ',' in foto else ('', foto)
                        img_data = base64.b64decode(base64_data)
                        img = Image.open(io.BytesIO(img_data))
                        img.thumbnail((120, 120))
                        img_io = io.BytesIO()
                        img.save(img_io, format='PNG')
                        img_io.seek(0)

                        img_excel = ExcelImage(img_io)
                        img_excel.anchor = f"F{row_index}"
                        ws.add_image(img_excel)

                        ws.row_dimensions[row_index].height = 90
                    except Exception as e:
                        print(f"Error en imagen de registro {idr}: {e}")
                row_index += 1

            # Ajustes de columnas
            ws.column_dimensions['A'].width = 12
            ws.column_dimensions['B'].width = 30
            ws.column_dimensions['C'].width = 25
            ws.column_dimensions['D'].width = 20
            ws.column_dimensions['E'].width = 35
            ws.column_dimensions['F'].width = 18

        # Generar archivo
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        return send_file(output,
                         download_name="proyectos_exportados.xlsx",
                         as_attachment=True,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        print(f"Error exportando proyectos: {e}")
        return "Error interno al exportar", 500
    finally:
        if conn:
            cursor.close()
            connection_pool.putconn(conn)

if __name__ == '__main__':
    app.run(debug=True)