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

DIAS_VALIDEZ_TOKEN = 30


# ------------------------------ CORS ---------------------------------

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


@api_movil.route("/api/movil/proyecto/<int:project_id>/registros", methods=["GET"])
@requiere_token
def api_proyecto_registros(project_id):
    """Historial de un proyecto desde respuestas_formulario."""
    from app import supabase_client
    u = request.usuario
    formulario_id = request.args.get("formulario_id", type=int)

    try:
        q = (supabase_client.table("respuestas_formulario")
             .select("id, formulario_id, id_proyecto, respuestas, created_at, user_id")
             .eq("id_proyecto", project_id)
             .order("created_at", desc=True)
             .limit(100))
        if formulario_id:
            q = q.eq("formulario_id", formulario_id)
        filas = q.execute().data or []

        # Nombres de formularios
        ids_form = list({f["formulario_id"] for f in filas})
        nombres = {}
        if ids_form:
            fr = (supabase_client.table("formularios")
                  .select("id, nombre").in_("id", ids_form).execute())
            nombres = {f["id"]: f.get("nombre", "Formulario") for f in (fr.data or [])}

        # Nombres de autores
        ids_user = list({f["user_id"] for f in filas if f.get("user_id")})
        autores = {}
        if ids_user:
            ur = (supabase_client.table("usuario")
                  .select("user_id, name, apellido").in_("user_id", ids_user).execute())
            for x in (ur.data or []):
                autores[x["user_id"]] = (f"{x.get('name','')} {x.get('apellido','')}").strip() or "Usuario"

        registros = []
        for r in filas:
            registros.append({
                "id": r["id"],
                "formulario_id": r["formulario_id"],
                "formulario_nombre": nombres.get(r["formulario_id"], "Formulario"),
                "preview": _preview_respuestas(r.get("respuestas")),
                "created_at": r.get("created_at"),
                "autor": autores.get(r.get("user_id"), "Usuario"),
            })
        return jsonify({"registros": registros})

    except Exception as e:
        current_app.logger.exception("api_proyecto_registros")
        return jsonify({"error": "error_servidor", "detalle": str(e)}), 500


def _preview_respuestas(respuestas):
    """Arma un texto corto con los primeros valores del registro."""
    if not isinstance(respuestas, dict):
        return ""
    valores = []
    for k, v in respuestas.items():
        if k != "__repeticiones" and v and isinstance(v, (str, int, float)):
            valores.append(str(v))
    for lista in (respuestas.get("__repeticiones") or {}).values():
        for rep in (lista or []):
            if isinstance(rep, dict):
                valores += [str(v) for v in rep.values()
                            if v and isinstance(v, (str, int, float))]
    return " · ".join(valores[:3])[:150]


@api_movil.route("/api/movil/proyecto/<int:project_id>/formulario/<int:formulario_id>/toggle",
                 methods=["POST"])
@requiere_token
def api_toggle_formulario(project_id, formulario_id):
    """Activa/desactiva un formulario para el usuario del token."""
    from app import supabase_client
    u = request.usuario
    uid = _num(u["uid"])
    empresa_id = _num(u["empresa_id"])
    activar = (request.get_json(silent=True) or {}).get("activar", True)

    try:
        if activar:
            # upsert equivalente al ON CONFLICT DO NOTHING
            existe = (supabase_client.table("proyecto_formularios_activos")
                      .select("id")
                      .eq("proyecto_id", project_id)
                      .eq("formulario_id", formulario_id)
                      .eq("user_id", uid)
                      .limit(1).execute())
            if not (existe.data or []):
                (supabase_client.table("proyecto_formularios_activos")
                 .insert({
                     "proyecto_id": project_id,
                     "formulario_id": formulario_id,
                     "user_id": uid,
                     "empresa_id": empresa_id,
                 }).execute())
        else:
            (supabase_client.table("proyecto_formularios_activos")
             .delete()
             .eq("proyecto_id", project_id)
             .eq("formulario_id", formulario_id)
             .eq("user_id", uid).execute())

        return jsonify({"success": True, "activo": activar})

    except Exception as e:
        current_app.logger.exception("api_toggle_formulario")
        return jsonify({"error": "error_servidor", "detalle": str(e)}), 500


@api_movil.route("/api/movil/formulario/<int:formulario_id>", methods=["GET"])
@requiere_token
def api_movil_formulario(formulario_id):
    """Definición COMPLETA de un formulario, lista para renderizar offline.
    Replica el cruce que hace /formulario-dinamico: formularios.campos son
    solo referencias; la definición real está en campos_globales."""
    from app import supabase_client
    u = request.usuario
    empresa_id = _num(u["empresa_id"])

    try:
        fr = (supabase_client.table("formularios")
              .select("id, nombre, descripcion, campos")
              .eq("id", formulario_id)
              .eq("empresa_id", empresa_id)
              .limit(1).execute())
        if not (fr.data or []):
            return jsonify({"error": "no_encontrado"}), 404
        form = fr.data[0]
        campos_config = form.get("campos") or []

        def es_grupo(item):
            return isinstance(item, dict) and item.get("tipo") == "grupo"

        # IDs de campos (los que no son grupo)
        campo_ids = [(item["id"] if isinstance(item, dict) else item)
                     for item in campos_config if not es_grupo(item)]

        catalogo = {}
        if campo_ids:
            cr = (supabase_client.table("campos_globales")
                  .select("id, nombre, tipo, opciones, configuracion")
                  .in_("id", campo_ids)
                  .eq("empresa_id", empresa_id).execute())
            catalogo = {c["id"]: {
                "id": c["id"], "nombre": c.get("nombre", ""),
                "tipo": c.get("tipo", "texto_corto"),
                "opciones": c.get("opciones") or [],
                "configuracion": c.get("configuracion") or {},
            } for c in (cr.data or [])}

        # Reconstruir el orden en secciones (sueltos y grupos), como la web
        secciones = []
        actual = None
        for item in campos_config:
            if es_grupo(item):
                actual = {
                    "tipo": "grupo",
                    "gid": item.get("gid") or "",
                    "nombre": item.get("nombre", "Grupo"),
                    "campos": [],
                }
                secciones.append(actual)
                continue
            cid = item["id"] if isinstance(item, dict) else item
            requerido = item.get("requerido", False) if isinstance(item, dict) else False
            base = catalogo.get(cid)
            if not base:
                continue
            campo = dict(base)
            campo["requerido"] = requerido
            if actual is None or actual["tipo"] != "grupo":
                if actual is None or actual["tipo"] != "sueltos":
                    actual = {"tipo": "sueltos", "gid": "", "nombre": "", "campos": []}
                    secciones.append(actual)
                actual["campos"].append(campo)
            else:
                actual["campos"].append(campo)

        return jsonify({
            "id": form["id"],
            "nombre": form.get("nombre", ""),
            "descripcion": form.get("descripcion") or "",
            "secciones": secciones,
        })

    except Exception as e:
        current_app.logger.exception("api_movil_formulario")
        return jsonify({"error": "error_servidor", "detalle": str(e)}), 500


@api_movil.route("/api/movil/respuestas-formulario", methods=["POST"])
@requiere_token
def api_movil_guardar_respuesta():
    """Crea un registro (respuesta de formulario) desde la app."""
    from app import supabase_client
    u = request.usuario
    uid = _num(u["uid"])
    empresa_id = _num(u["empresa_id"])
    data = request.get_json(silent=True) or {}

    formulario_id = data.get("formulario_id")
    project_id = data.get("project_id")
    respuestas = data.get("respuestas")

    if not formulario_id or not project_id or respuestas is None:
        return jsonify({"error": "datos_incompletos"}), 400

    try:
        fila = {
            "formulario_id": _num(formulario_id),
            "id_proyecto": _num(project_id),
            "respuestas": respuestas,
            "user_id": uid,
        }
        # id_local: si la app lo manda, sirve para evitar duplicados al
        # reintentar la subida desde el outbox (idempotencia).
        id_local = data.get("id_local")
        if id_local:
            existe = (supabase_client.table("respuestas_formulario")
                      .select("id").eq("id_local", id_local).limit(1).execute())
            if existe.data:
                return jsonify({"success": True, "id": existe.data[0]["id"], "duplicado": True})
            fila["id_local"] = id_local

        r = supabase_client.table("respuestas_formulario").insert(fila).execute()
        nuevo_id = (r.data or [{}])[0].get("id")
        return jsonify({"success": True, "id": nuevo_id})

    except Exception as e:
        current_app.logger.exception("api_movil_guardar_respuesta")
        return jsonify({"error": "error_servidor", "detalle": str(e)}), 500


@api_movil.route("/api/movil/registro/<int:registro_id>", methods=["GET"])
@requiere_token
def api_movil_registro(registro_id):
    """Un registro individual (respuestas + metadatos) para verlo lleno."""
    from app import supabase_client
    u = request.usuario
    empresa_id = _num(u["empresa_id"])

    try:
        r = (supabase_client.table("respuestas_formulario")
             .select("id, formulario_id, id_proyecto, respuestas, user_id, created_at")
             .eq("id", registro_id).limit(1).execute())
        if not (r.data or []):
            return jsonify({"error": "no_encontrado"}), 404
        reg = r.data[0]

        # Verificar que el registro pertenezca a un proyecto de la empresa
        # del usuario (barrera de tenant, ya que la tabla no tiene empresa_id).
        proy = (supabase_client.table("proyectos")
                .select("empresa_id")
                .eq("id", reg["id_proyecto"]).limit(1).execute())
        if proy.data and _num(proy.data[0].get("empresa_id")) != empresa_id:
            return jsonify({"error": "no_autorizado"}), 403

        autor = "Usuario"
        if reg.get("user_id"):
            ur = (supabase_client.table("usuario")
                  .select("name, apellido").eq("user_id", reg["user_id"]).limit(1).execute())
            if ur.data:
                autor = (f"{ur.data[0].get('name','')} {ur.data[0].get('apellido','')}").strip() or "Usuario"

        return jsonify({
            "id": reg["id"],
            "formulario_id": reg["formulario_id"],
            "id_proyecto": reg["id_proyecto"],
            "respuestas": reg.get("respuestas") or {},
            "autor": autor,
            "es_autor": _num(reg.get("user_id")) == _num(u["uid"]),
            "created_at": reg.get("created_at"),
        })

    except Exception as e:
        current_app.logger.exception("api_movil_registro")
        return jsonify({"error": "error_servidor", "detalle": str(e)}), 500


# ==================== CONFIGURACIÓN (solo admin) =====================

def _es_admin(u):
    return (u.get("rol") or "").lower() in ("admin", "administrador")


@api_movil.route("/api/movil/config", methods=["GET"])
@requiere_token
def api_movil_config():
    """Trae en una sola llamada todo lo que muestra Configuración:
       identidad visual, usuarios, tipos de proyecto, campos y formularios.
       Solo lectura — Fase 1."""
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u):
        return jsonify({"error": "solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])

    out = {}
    try:
        # Identidad visual
        emp = (supabase_client.table("empresas")
               .select("logo_url, color_primario, color_secundario")
               .eq("id", empresa_id).limit(1).execute())
        e = (emp.data or [{}])[0]
        out["identidad"] = {
            "logo_url": e.get("logo_url"),
            "color_primario": e.get("color_primario") or "#FBAF33",
            "color_secundario": e.get("color_secundario") or "#E3E3E3",
        }

        # Usuarios del tenant
        us = (supabase_client.table("usuario")
              .select("user_id, name, apellido, email, rol, estado")
              .eq("empresa_id", empresa_id).order("name").execute())
        out["usuarios"] = [{
            "user_id": x["user_id"], "nombre": x.get("name",""),
            "apellido": x.get("apellido","") or "", "email": x.get("email",""),
            "rol": x.get("rol") or "Sin rol", "estado": x.get("estado") or "pendiente",
        } for x in (us.data or [])]

        # Tipos de proyecto
        tp = (supabase_client.table("tipos_proyecto")
              .select("*").eq("empresa_id", empresa_id).execute())
        out["tipos_proyecto"] = tp.data or []

        # Campos globales
        cg = (supabase_client.table("campos_globales")
              .select("id, nombre, tipo, objeto, opciones, configuracion")
              .eq("empresa_id", empresa_id).order("nombre").execute())
        out["campos_globales"] = cg.data or []

        # Formularios (lista, sin resolver campos)
        fm = (supabase_client.table("formularios")
              .select("id, nombre, descripcion, campos" )
              .eq("empresa_id", empresa_id).order("nombre").execute())
        out["formularios"] = fm.data or []

        return jsonify(out)

    except Exception as ex:
        current_app.logger.exception("api_movil_config")
        return jsonify({"error": "error_servidor", "detalle": str(ex)}), 500


# ======================= VOZ / IA (reusa app.py) =====================
# La lógica de distribución (prompt + OpenAI) vive en app.py, en una
# función PURA que solo depende del JSON, no de session. Aquí la
# llamamos tras validar el token. Sin trucos de sesión.

@api_movil.route("/api/movil/distribuir-campos", methods=["POST"])
@requiere_token
def api_movil_distribuir():
    import app as appmod
    data = request.get_json(silent=True) or {}
    try:
        # distribuir_campos_core devuelve un dict listo para jsonify
        resultado, codigo = appmod.distribuir_campos_core(data)
        return jsonify(resultado), codigo
    except Exception as e:
        current_app.logger.exception("api_movil_distribuir")
        return jsonify({"error": "error_servidor", "detalle": str(e)}), 500


# ==================== IDENTIDAD VISUAL ================================

@api_movil.route("/api/movil/subir-logo", methods=["POST"])
@requiere_token
def api_movil_subir_logo():
    """Sube un logo (base64) a Supabase Storage y guarda en empresa_logos."""
    from app import supabase_client, SUPABASE_URL
    import base64, uuid
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])
    data = request.get_json(silent=True) or {}
    file_data = data.get("imagen")
    if not file_data: return jsonify({"error":"No se recibió imagen"}), 400
    try:
        if "," in file_data:
            header, b64 = file_data.split(",", 1)
            ext = "png" if "png" in header else "jpg"
        else:
            b64, ext = file_data, "jpg"
        imagen_bytes = base64.b64decode(b64)
        nombre = f"{uuid.uuid4()}.{ext}"
        ruta = f"logos/{empresa_id}/{nombre}"
        supabase_client.storage.from_("fotos-bitacora").upload(
            ruta, imagen_bytes, {"content-type": f"image/{ext}"})
        url_publica = f"{SUPABASE_URL}/storage/v1/object/public/fotos-bitacora/{ruta}"
        supabase_client.table("empresa_logos").insert({
            "empresa_id": empresa_id, "url": url_publica,
            "creado_por": _num(u["uid"])}).execute()
        return jsonify({"url": url_publica})
    except Exception as e:
        current_app.logger.exception("api_movil_subir_logo")
        return jsonify({"error": str(e)}), 500


@api_movil.route("/api/movil/logos-empresa", methods=["GET"])
@requiere_token
def api_movil_logos():
    """Lista los logos subidos de la empresa."""
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])
    try:
        r = (supabase_client.table("empresa_logos")
             .select("id, url, created_at")
             .eq("empresa_id", empresa_id)
             .order("created_at", desc=True).execute())
        logos = [{"id":l["id"], "url":l["url"],
                  "fecha":l.get("created_at","")[:10]} for l in (r.data or [])]
        return jsonify({"logos": logos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_movil.route("/api/movil/guardar-configuracion", methods=["POST"])
@requiere_token
def api_movil_guardar_config():
    """Guarda colores y logo de la empresa."""
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])
    data = request.get_json(silent=True) or {}
    try:
        (supabase_client.table("empresas").update({
            "color_primario": data.get("color_primario", "#FBAF33"),
            "color_secundario": data.get("color_secundario", "#E3E3E3"),
            "logo_url": data.get("logo", ""),
        }).eq("id", empresa_id).execute())
        return jsonify({"success": True})
    except Exception as e:
        current_app.logger.exception("api_movil_guardar_config")
        return jsonify({"error": str(e)}), 500


# ==================== TIPOS DE PROYECTO ===============================

@api_movil.route("/api/movil/tipos-proyecto", methods=["GET"])
@requiere_token
def api_movil_tipos_proyecto():
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])
    try:
        r = (supabase_client.table("tipos_proyecto")
             .select("*").eq("empresa_id", empresa_id).execute())
        return jsonify({"tipos": r.data or []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_movil.route("/api/movil/tipos-proyecto", methods=["POST"])
@requiere_token
def api_movil_crear_tipo():
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    data = request.get_json(silent=True) or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre: return jsonify({"error":"Nombre obligatorio"}), 400
    try:
        r = (supabase_client.table("tipos_proyecto").insert({
            "empresa_id": _num(u["empresa_id"]), "nombre": nombre,
            "descripcion": data.get("descripcion",""), "campos": data.get("campos",[])
        }).execute())
        return jsonify({"success":True, "id":(r.data or [{}])[0].get("id")})
    except Exception as e: return jsonify({"error":str(e)}), 500

@api_movil.route("/api/movil/tipos-proyecto/<int:tid>", methods=["PUT"])
@requiere_token
def api_movil_editar_tipo(tid):
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    data = request.get_json(silent=True) or {}
    try:
        (supabase_client.table("tipos_proyecto").update({
            "nombre": (data.get("nombre") or "").strip(),
            "descripcion": data.get("descripcion",""), "campos": data.get("campos",[])
        }).eq("id", tid).eq("empresa_id", _num(u["empresa_id"])).execute())
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}), 500

@api_movil.route("/api/movil/tipos-proyecto/<int:tid>", methods=["DELETE"])
@requiere_token
def api_movil_eliminar_tipo(tid):
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    try:
        (supabase_client.table("tipos_proyecto").delete()
         .eq("id", tid).eq("empresa_id", _num(u["empresa_id"])).execute())
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}), 500


# ==================== FORMULARIOS =====================================

@api_movil.route("/api/movil/formularios", methods=["GET"])
@requiere_token
def api_movil_get_formularios():
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    try:
        r = (supabase_client.table("formularios")
             .select("id, nombre, descripcion, campos, created_at")
             .eq("empresa_id", _num(u["empresa_id"])).order("created_at", desc=True).execute())
        return jsonify({"formularios": r.data or []})
    except Exception as e: return jsonify({"error":str(e)}), 500

@api_movil.route("/api/movil/formularios", methods=["POST"])
@requiere_token
def api_movil_crear_formulario():
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    data = request.get_json(silent=True) or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre: return jsonify({"error":"Nombre obligatorio"}), 400
    try:
        r = (supabase_client.table("formularios").insert({
            "empresa_id": _num(u["empresa_id"]), "nombre": nombre,
            "descripcion": (data.get("descripcion") or "").strip(),
            "campos": data.get("campos",[])
        }).execute())
        return jsonify({"success":True, "id":(r.data or [{}])[0].get("id")})
    except Exception as e: return jsonify({"error":str(e)}), 500

@api_movil.route("/api/movil/formularios/<int:fid>", methods=["PUT"])
@requiere_token
def api_movil_editar_formulario(fid):
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    data = request.get_json(silent=True) or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre: return jsonify({"error":"Nombre obligatorio"}), 400
    try:
        (supabase_client.table("formularios").update({
            "nombre": nombre, "descripcion": (data.get("descripcion") or "").strip(),
            "campos": data.get("campos",[])
        }).eq("id", fid).eq("empresa_id", _num(u["empresa_id"])).execute())
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}), 500

@api_movil.route("/api/movil/formularios/<int:fid>", methods=["DELETE"])
@requiere_token
def api_movil_eliminar_formulario(fid):
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    try:
        (supabase_client.table("formularios").delete()
         .eq("id", fid).eq("empresa_id", _num(u["empresa_id"])).execute())
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}), 500


@api_movil.route("/api/movil/campos-globales", methods=["GET"])
@requiere_token
def api_movil_get_campos():
    """Lista campos globales, con filtro opcional por objeto."""
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u): return jsonify({"error":"solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])
    objeto = request.args.get("objeto")
    try:
        q = (supabase_client.table("campos_globales")
             .select("id, nombre, tipo, objeto, opciones, configuracion")
             .eq("empresa_id", empresa_id).order("nombre"))
        if objeto: q = q.eq("objeto", objeto)
        return jsonify({"campos": q.execute().data or []})
    except Exception as e: return jsonify({"error":str(e)}), 500


# ==================== CAMPOS GLOBALES =================================

@api_movil.route("/api/movil/campos-globales", methods=["POST"])
@requiere_token
def api_movil_crear_campo():
    """Crea un campo global."""
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u):
        return jsonify({"error": "solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])
    data = request.get_json(silent=True) or {}

    nombre = (data.get("nombre") or "").strip()
    tipo = data.get("tipo")
    objeto = data.get("objeto", "formulario")
    opciones = data.get("opciones", [])
    configuracion = data.get("configuracion", {})

    if not nombre or not tipo:
        return jsonify({"error": "Nombre y tipo son obligatorios"}), 400
    if objeto not in ("formulario", "proyecto"):
        return jsonify({"error": "Objeto inválido"}), 400

    try:
        r = (supabase_client.table("campos_globales")
             .insert({
                 "empresa_id": empresa_id, "nombre": nombre, "tipo": tipo,
                 "objeto": objeto, "opciones": opciones, "configuracion": configuracion
             }).execute())
        nuevo_id = (r.data or [{}])[0].get("id")
        return jsonify({"success": True, "id": nuevo_id})
    except Exception as e:
        current_app.logger.exception("api_movil_crear_campo")
        return jsonify({"error": str(e)}), 500


@api_movil.route("/api/movil/campos-globales/<int:campo_id>", methods=["PUT"])
@requiere_token
def api_movil_editar_campo(campo_id):
    """Edita un campo global existente."""
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u):
        return jsonify({"error": "solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])
    data = request.get_json(silent=True) or {}

    nombre = (data.get("nombre") or "").strip()
    tipo = data.get("tipo")
    objeto = data.get("objeto", "formulario")
    opciones = data.get("opciones", [])
    configuracion = data.get("configuracion", {})

    if not nombre or not tipo:
        return jsonify({"error": "Nombre y tipo son obligatorios"}), 400

    try:
        (supabase_client.table("campos_globales")
         .update({"nombre": nombre, "tipo": tipo, "objeto": objeto,
                  "opciones": opciones, "configuracion": configuracion})
         .eq("id", campo_id).eq("empresa_id", empresa_id).execute())
        return jsonify({"success": True})
    except Exception as e:
        current_app.logger.exception("api_movil_editar_campo")
        return jsonify({"error": str(e)}), 500


@api_movil.route("/api/movil/campos-globales/<int:campo_id>", methods=["DELETE"])
@requiere_token
def api_movil_eliminar_campo(campo_id):
    """Elimina un campo global."""
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u):
        return jsonify({"error": "solo_admin"}), 403
    empresa_id = _num(u["empresa_id"])
    try:
        (supabase_client.table("campos_globales")
         .delete().eq("id", campo_id).eq("empresa_id", empresa_id).execute())
        return jsonify({"success": True})
    except Exception as e:
        current_app.logger.exception("api_movil_eliminar_campo")
        return jsonify({"error": str(e)}), 500


# ==================== GESTIÓN DE USUARIOS ============================

@api_movil.route("/api/movil/invitar-usuarios", methods=["POST"])
@requiere_token
def api_movil_invitar():
    """Invita usuarios — llama al core puro de app.py, sin sesión."""
    import app as appmod
    u = request.usuario
    if not _es_admin(u):
        return jsonify({"error": "solo_admin"}), 403

    data = request.get_json(silent=True) or {}
    try:
        resultado, codigo = appmod.invitar_usuarios_core(
            data, _num(u["uid"]), _num(u["empresa_id"])
        )
        return jsonify(resultado), codigo
    except Exception as e:
        current_app.logger.exception("api_movil_invitar")
        return jsonify({"error": "error_servidor", "detalle": str(e)}), 500


@api_movil.route("/api/movil/eliminar-usuario/<int:uid>", methods=["DELETE"])
@requiere_token
def api_movil_eliminar_usuario(uid):
    """Elimina un usuario de la organización."""
    from app import supabase_client
    u = request.usuario
    if not _es_admin(u):
        return jsonify({"error": "solo_admin"}), 403

    admin_uid = _num(u["uid"])
    empresa_id = _num(u["empresa_id"])

    if uid == admin_uid:
        return jsonify({"success": False, "error": "No puedes eliminarte a ti mismo"})

    try:
        # Verificar que el usuario pertenece a la misma empresa
        check = (supabase_client.table("usuario")
                 .select("user_id").eq("user_id", uid)
                 .eq("empresa_id", empresa_id).limit(1).execute())
        if not (check.data or []):
            return jsonify({"success": False, "error": "Usuario no encontrado en tu organización"})

        supabase_client.table("usuario").delete().eq("user_id", uid).execute()
        return jsonify({"success": True})

    except Exception as e:
        current_app.logger.exception("api_movil_eliminar_usuario")
        return jsonify({"success": False, "error": str(e)}), 500

@api_movil.route("/api/movil/upload-foto", methods=["POST"])
@requiere_token
def api_movil_upload_foto():
    """Sube una imagen en base64 a Supabase Storage."""
    from app import supabase_client, SUPABASE_URL
    import base64, uuid
    data = request.get_json(silent=True) or {}
    file_data = data.get('file_data', '')
    if not file_data:
        return jsonify({"error": "No se recibió imagen"}), 400
    try:
        if ',' in file_data:
            header, b64 = file_data.split(',', 1)
            if 'png' in header:   ext, mime = 'png', 'image/png'
            elif 'webp' in header: ext, mime = 'webp', 'image/webp'
            else:                  ext, mime = 'jpg', 'image/jpeg'
        else:
            b64, ext, mime = file_data, 'jpg', 'image/jpeg'
        imagen_bytes   = base64.b64decode(b64)
        nombre_archivo = f"{uuid.uuid4()}.{ext}"
        ruta           = f"registros/{nombre_archivo}"
        supabase_client.storage.from_('fotos-bitacora').upload(
            ruta, imagen_bytes, {"content-type": mime}
        )
        url_publica = f"{SUPABASE_URL}/storage/v1/object/public/fotos-bitacora/{ruta}"
        return jsonify({"url": url_publica}), 200
    except Exception as e:
        current_app.logger.exception("api_movil_upload_foto")
        return jsonify({"error": str(e)}), 500


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

