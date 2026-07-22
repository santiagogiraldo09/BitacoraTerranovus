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
#  ADAPTADO A TU CÓDIGO
#  ---------------------------------------------------------------------
#  Reutiliza verify_user() de app.py: la app móvil valida EXACTAMENTE
#  igual que tu web, así no hay dos comportamientos distintos.
# =====================================================================

# ¿Los formularios activos son por usuario o por proyecto?
# Tu tabla proyecto_formularios_activos tiene user_id, así que si quieres
# que cada quien vea solo los que él activó, pon esto en True.
FILTRAR_FORMULARIOS_POR_USUARIO = False


def _num(v):
    """Los ids viajan como texto en el token; la BD los tiene como integer."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _primer_valor(d, *claves, default=""):
    for c in claves:
        if d.get(c) not in (None, ""):
            return d[c]
    return default


def _validar_credenciales(email, password):
    from app import verify_user

    u = verify_user(email, password)
    if not u:
        return None

    # Usuario que todavía no ha cambiado su contraseña inicial:
    # en la web lo mandas a cambiar_password_page. Desde la app lo
    # bloqueamos y le decimos que lo haga por el navegador.
    if u.get("estado") == "pendiente":
        return {"__pendiente__": True}

    return {
        "id": u["user_id"],
        "correo": email,
        "nombre": u.get("name") or email,
        "rol": (u.get("rol") or "viewer").lower(),
        "empresa_id": u["empresa_id"],
        "empresa_nombre": _nombre_empresa(u["empresa_id"]),
    }


def _nombre_empresa(empresa_id):
    from app import supabase_client
    try:
        r = (supabase_client.table("empresas")
             .select("*")
             .eq("id", _num(empresa_id))
             .limit(1)
             .execute())
        if r.data:
            return _primer_valor(r.data[0], "nombre", "nombre_empresa", "name",
                                 default="")
    except Exception:
        pass
    return ""


def _proyectos_del_usuario(uid, empresa_id):
    from app import supabase_client

    uid = _num(uid)
    empresa_id = _num(empresa_id)

    # 1) Proyectos asignados a ESTE usuario
    asign = (supabase_client.table("proyecto_usuarios")
             .select("id_proyecto")
             .eq("user_id", uid)
             .eq("empresa_id", empresa_id)
             .execute())

    ids = list({a["id_proyecto"] for a in (asign.data or [])})
    if not ids:
        return []

    # 2) Datos de esos proyectos
    proy = (supabase_client.table("proyectos")
            .select("*")
            .in_("id", ids)
            .eq("empresa_id", empresa_id)
            .execute())

    # 3) Formularios activos de esos proyectos
    q = (supabase_client.table("proyecto_formularios_activos")
         .select("proyecto_id, formulario_id, activated_at")
         .in_("proyecto_id", ids)
         .eq("empresa_id", empresa_id))
    if FILTRAR_FORMULARIOS_POR_USUARIO:
        q = q.eq("user_id", uid)
    activos = q.execute()

    # 4) Nombres de los formularios
    ids_form = list({a["formulario_id"] for a in (activos.data or [])})
    catalogo = {}
    if ids_form:
        forms = (supabase_client.table("formularios")
                 .select("id, nombre, descripcion")
                 .in_("id", ids_form)
                 .execute())
        catalogo = {f["id"]: f for f in (forms.data or [])}

    # 5) Agrupar formularios por proyecto (el más reciente queda destacado)
    por_proyecto = {}
    for a in (activos.data or []):
        f = catalogo.get(a["formulario_id"], {})
        por_proyecto.setdefault(a["proyecto_id"], []).append({
            "id": str(a["formulario_id"]),
            "nombre": f.get("nombre") or "Formulario",
            "subtexto": f.get("descripcion") or "",
            "_orden": a.get("activated_at") or "",
        })

    for lista in por_proyecto.values():
        lista.sort(key=lambda x: x["_orden"], reverse=True)
        if lista:
            lista[0]["es_ultimo"] = True
        for f in lista:
            f.pop("_orden", None)

    # 6) Armar la respuesta
    salida = []
    for p in (proy.data or []):
        pid = p["id"]
        salida.append({
            "id_proyecto": str(pid),
            "name": _primer_valor(p, "nombre_proyecto", "name", "nombre",
                                  default="Sin nombre"),
            "estado": _primer_valor(p, "estado", "status", "estado_proyecto",
                                    default="Activo"),
            "formularios": por_proyecto.get(pid, []),
        })

    salida.sort(key=lambda x: x["name"].lower())
    return salida


# ----------------------------- RUTAS ---------------------------------

# ⚠️ TEMPORAL: pon False cuando termines de depurar
DEBUG_API = True


@api_movil.route("/api/login", methods=["POST"])
def api_login():
    import traceback

    datos = request.get_json(silent=True) or {}
    email = (datos.get("email") or "").strip()
    password = datos.get("password") or ""

    if not email or not password:
        return jsonify({"error": "datos_incompletos"}), 400

    try:
        perfil = _validar_credenciales(email, password)
    except Exception as e:
        current_app.logger.exception("api_login")
        if DEBUG_API:
            return jsonify({
                "error": "fallo_validacion",
                "tipo": type(e).__name__,
                "detalle": str(e),
                "traceback": traceback.format_exc().splitlines()[-6:]
            }), 500
        return jsonify({"error": "error_servidor"}), 500
    if not perfil:
        return jsonify({"error": "credenciales_invalidas"}), 401

    if perfil.get("__pendiente__"):
        return jsonify({
            "error": "password_pendiente",
            "mensaje": "Debes cambiar tu contraseña inicial desde el navegador antes de usar la app."
        }), 403

    try:
        token = _crear_token(perfil)
    except Exception as e:
        current_app.logger.exception("crear_token")
        if DEBUG_API:
            return jsonify({
                "error": "fallo_token",
                "detalle": str(e),
                "pista": "Revisa que app.secret_key esté configurada"
            }), 500
        return jsonify({"error": "error_servidor"}), 500

    return jsonify({"token": token, "perfil": perfil})


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
