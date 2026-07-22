# =====================================================================
#  api_movil.py  —  ENDPOINTS PARA LA APP MÓVIL
#  ---------------------------------------------------------------------
#  Se registra como Blueprint. NO toca ninguna de tus rutas actuales.
#
#  Por qué endpoints nuevos y no reusar /login:
#    · /login responde con redirect + cookie de sesión → sirve al navegador,
#      no a una app. La app necesita JSON + token.
#    · La app corre en otro origen (localhost del WebView) → hace falta CORS.
#
#  Solo hay DOS bloques que debes adaptar a tu base. Están marcados
#  con  ### ADAPTAR ###
# =====================================================================

from flask import Blueprint, request, jsonify, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash
from functools import wraps

api_movil = Blueprint("api_movil", __name__)

ORIGENES_APP = {
    "http://localhost", "https://localhost",
    "capacitor://localhost", "ionic://localhost",
}
DIAS_VALIDEZ_TOKEN = 30


# ------------------------------ CORS ---------------------------------

@api_movil.after_request
def _cors(resp):
    origen = request.headers.get("Origin", "")
    if origen in ORIGENES_APP:
        resp.headers["Access-Control-Allow-Origin"] = origen
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@api_movil.route("/api/<path:_ruta>", methods=["OPTIONS"])
def _preflight(_ruta):
    return ("", 204)


# ------------------------------ TOKEN --------------------------------

def _firmador():
    return URLSafeTimedSerializer(current_app.secret_key, salt="bitacora-movil")


def _crear_token(perfil):
    return _firmador().dumps({
        "uid": str(perfil["id"]),
        "empresa_id": str(perfil["empresa_id"]),
        "rol": perfil["rol"],
    })


def requiere_token(f):
    @wraps(f)
    def envoltura(*args, **kwargs):
        cabecera = request.headers.get("Authorization", "")
        if not cabecera.startswith("Bearer "):
            return jsonify({"error": "token_faltante"}), 401
        try:
            datos = _firmador().loads(
                cabecera[7:], max_age=DIAS_VALIDEZ_TOKEN * 86400
            )
        except SignatureExpired:
            return jsonify({"error": "token_expirado"}), 401
        except BadSignature:
            return jsonify({"error": "token_invalido"}), 401
        request.usuario = datos
        return f(*args, **kwargs)
    return envoltura


# =====================================================================
#  ### ADAPTAR 1 ###  Validación de credenciales
#  ---------------------------------------------------------------------
#  Reemplaza el cuerpo por la MISMA lógica que ya usa tu ruta /login.
#  Debe devolver un dict con estas llaves, o None si no es válido.
# =====================================================================

def _validar_credenciales(email, password):
    from app import supabase          # ← ajusta al import real de tu cliente

    res = (supabase.table("usuarios")        # ← nombre real de tu tabla
           .select("*")
           .eq("email", email)
           .limit(1)
           .execute())

    filas = res.data or []
    if not filas:
        return None
    u = filas[0]

    # ← usa el MISMO método de verificación que tu /login actual
    if not check_password_hash(u.get("password", ""), password):
        return None

    return {
        "id": u["id"],
        "correo": u["email"],
        "nombre": u.get("nombre") or u.get("user_name") or email,
        "rol": (u.get("rol") or "viewer").lower(),
        "empresa_id": u["empresa_id"],
        "empresa_nombre": u.get("empresa_nombre", ""),
    }


# =====================================================================
#  ### ADAPTAR 2 ###  Proyectos asignados al usuario
#  ---------------------------------------------------------------------
#  Devuelve SOLO los proyectos donde el usuario está asignado
#  (tabla proyecto_usuarios), con sus formularios activos.
#  Ajusta los nombres de columnas si difieren.
# =====================================================================

def _proyectos_del_usuario(uid, empresa_id):
    from app import supabase

    asign = (supabase.table("proyecto_usuarios")
             .select("id_proyecto")
             .eq("user_id", uid)
             .eq("empresa_id", empresa_id)
             .execute())

    ids = [a["id_proyecto"] for a in (asign.data or [])]
    if not ids:
        return []

    proy = (supabase.table("proyectos")
            .select("*")
            .in_("id_proyecto", ids)
            .eq("empresa_id", empresa_id)
            .execute())

    activos = (supabase.table("proyecto_formularios_activos")
               .select("id_proyecto, formulario_id")
               .in_("id_proyecto", ids)
               .eq("empresa_id", empresa_id)
               .execute())

    ids_form = list({a["formulario_id"] for a in (activos.data or [])})
    catalogo = {}
    if ids_form:
        forms = (supabase.table("formularios")
                 .select("id, nombre")
                 .in_("id", ids_form)
                 .execute())
        catalogo = {f["id"]: f.get("nombre", "Formulario") for f in (forms.data or [])}

    por_proyecto = {}
    for a in (activos.data or []):
        por_proyecto.setdefault(a["id_proyecto"], []).append({
            "id": str(a["formulario_id"]),
            "nombre": catalogo.get(a["formulario_id"], "Formulario"),
            "subtexto": "",
        })

    salida = []
    for p in (proy.data or []):
        pid = p["id_proyecto"]
        salida.append({
            "id_proyecto": str(pid),
            "name": p.get("name") or p.get("nombre", ""),
            "estado": p.get("estado") or "Activo",
            "formularios": por_proyecto.get(pid, []),
        })
    return salida


# ----------------------------- RUTAS ---------------------------------

@api_movil.route("/api/login", methods=["POST"])
def api_login():
    datos = request.get_json(silent=True) or {}
    email = (datos.get("email") or "").strip()
    password = datos.get("password") or ""

    if not email or not password:
        return jsonify({"error": "datos_incompletos"}), 400

    perfil = _validar_credenciales(email, password)
    if not perfil:
        return jsonify({"error": "credenciales_invalidas"}), 401

    return jsonify({"token": _crear_token(perfil), "perfil": perfil})


@api_movil.route("/api/proyectos", methods=["GET"])
@requiere_token
def api_proyectos():
    u = request.usuario
    try:
        proyectos = _proyectos_del_usuario(u["uid"], u["empresa_id"])
    except Exception as e:
        current_app.logger.exception("api_proyectos")
        return jsonify({"error": "error_servidor", "detalle": str(e)}), 500

    return jsonify({"proyectos": proyectos})


@api_movil.route("/api/ping", methods=["GET"])
def api_ping():
    return jsonify({"ok": True})
