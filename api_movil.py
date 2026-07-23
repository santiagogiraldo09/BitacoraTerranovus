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
    # Si Flask-CORS ya puso la cabecera, NO la duplicamos:
    # dos Access-Control-Allow-Origin hacen que el navegador bloquee todo.
    if resp.headers.get("Access-Control-Allow-Origin"):
        return resp

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


# Si un usuario no tiene proyectos por ninguna de las dos vías,
# ¿mostrarle todos los de su empresa? Útil para probar.
INCLUIR_TODOS_DE_EMPRESA_SI_VACIO = False


def _ids_asignados(uid, empresa_id):
    """Busca los proyectos del usuario por las DOS vías posibles."""
    from app import supabase_client
    ids = set()
    detalle = {}

    # a) Tabla de asignación
    try:
        r = (supabase_client.table("proyecto_usuarios")
             .select("id_proyecto")
             .eq("user_id", uid)
             .execute())
        v = {a["id_proyecto"] for a in (r.data or [])}
        detalle["proyecto_usuarios"] = len(v)
        ids |= v
    except Exception as e:
        detalle["proyecto_usuarios_error"] = str(e)

    # b) Proyectos creados por el usuario (columna user_id en proyectos)
    try:
        r = (supabase_client.table("proyectos")
             .select("id")
             .eq("user_id", uid)
             .execute())
        v = {p["id"] for p in (r.data or [])}
        detalle["proyectos_user_id"] = len(v)
        ids |= v
    except Exception as e:
        detalle["proyectos_user_id_error"] = str(e)

    # c) Respaldo opcional: todos los de la empresa
    if not ids and INCLUIR_TODOS_DE_EMPRESA_SI_VACIO:
        r = (supabase_client.table("proyectos")
             .select("id")
             .eq("empresa_id", empresa_id)
             .execute())
        ids |= {p["id"] for p in (r.data or [])}
        detalle["respaldo_empresa"] = len(ids)

    return list(ids), detalle


def _proyectos_del_usuario(uid, empresa_id):
    from app import supabase_client

    uid = _num(uid)
    empresa_id = _num(empresa_id)

    ids, _ = _ids_asignados(uid, empresa_id)
    if not ids:
        return []

    proy = (supabase_client.table("proyectos")
            .select("*")
            .in_("id", ids)
            .eq("empresa_id", empresa_id)
            .execute())

    q = (supabase_client.table("proyecto_formularios_activos")
         .select("proyecto_id, formulario_id, activated_at")
         .in_("proyecto_id", ids))
    if FILTRAR_FORMULARIOS_POR_USUARIO:
        q = q.eq("user_id", uid)
    activos = q.execute()

    ids_form = list({a["formulario_id"] for a in (activos.data or [])})
    catalogo = {}
    if ids_form:
        forms = (supabase_client.table("formularios")
                 .select("id, nombre, descripcion")
                 .in_("id", ids_form)
                 .execute())
        catalogo = {f["id"]: f for f in (forms.data or [])}

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


# ---------------------- DIAGNÓSTICO (temporal) -----------------------
#  Ábrelo en el navegador:
#    https://TU-APP.onrender.com/api/diagnostico?token=EL_TOKEN
#  Borra esta ruta cuando termines de depurar.
# ---------------------------------------------------------------------

@api_movil.route("/api/diagnostico", methods=["GET"])
def api_diagnostico():
    from app import supabase_client

    # Modo sin token (solo mientras DEBUG_API esté activo):
    #   /api/diagnostico?uid=1&empresa=1
    uid_param = request.args.get("uid")
    if uid_param and DEBUG_API:
        uid = _num(uid_param)
        emp = _num(request.args.get("empresa", "1"))
    else:
        token = request.args.get("token") or request.headers.get("Authorization", "")[7:]
        try:
            u = _firmador().loads(token, max_age=DIAS_VALIDEZ_TOKEN * 86400)
        except Exception as e:
            return jsonify({"error": "token_invalido", "detalle": str(e)}), 401
        uid = _num(u["uid"])
        emp = _num(u["empresa_id"])
    info = {"user_id": uid, "empresa_id": emp}

    ids, detalle = _ids_asignados(uid, emp)
    info["busqueda"] = detalle
    info["ids_encontrados"] = ids[:20]
    info["total_ids"] = len(ids)

    try:
        r = (supabase_client.table("proyectos").select("id")
             .eq("empresa_id", emp).execute())
        info["proyectos_en_la_empresa"] = len(r.data or [])
    except Exception as e:
        info["proyectos_en_la_empresa_error"] = str(e)

    try:
        r = (supabase_client.table("proyectos").select("*")
             .eq("empresa_id", emp).limit(1).execute())
        if r.data:
            info["columnas_de_proyectos"] = sorted(r.data[0].keys())
    except Exception as e:
        info["columnas_error"] = str(e)

    info["proyectos_devueltos"] = len(_proyectos_del_usuario(uid, emp))
    return jsonify(info)
