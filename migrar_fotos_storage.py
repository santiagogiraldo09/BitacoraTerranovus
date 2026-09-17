from app import supabase_client


BUCKET = "fotos-bitacora"

# ============================================================
# SEGURIDAD
# ============================================================
# True  = solo simula. NO copia archivos.
# False = realiza las copias.
DRY_RUN = False


def obtener_fotos_a_migrar():
    registros = (
        supabase_client
        .table("respuestas_formulario")
        .select("id,id_proyecto,respuestas")
        .execute()
        .data
        or []
    )

    proyectos = (
        supabase_client
        .table("proyectos")
        .select("id,empresa_id")
        .execute()
        .data
        or []
    )

    empresa_por_proyecto = {
        int(p["id"]): int(p["empresa_id"])
        for p in proyectos
        if p.get("id") is not None
        and p.get("empresa_id") is not None
    }

    fotos = {}

    def recorrer(valor, empresa_id, registro_id):
        if isinstance(valor, dict):
            for v in valor.values():
                recorrer(v, empresa_id, registro_id)
            return

        if isinstance(valor, list):
            for v in valor:
                recorrer(v, empresa_id, registro_id)
            return

        if not isinstance(valor, str):
            return

        marcador = "/fotos-bitacora/"

        if marcador not in valor:
            return

        ruta = valor.split(marcador, 1)[1]

        if not ruta.startswith("registros/"):
            return

        prefijo_empresa = f"registros/{empresa_id}/"

        # Ya tiene la estructura nueva.
        if ruta.startswith(prefijo_empresa):
            return

        resto = ruta[len("registros/"):]

        # Por seguridad solamente migramos el formato histórico:
        #
        # registros/archivo.jpg
        #
        # Si encontramos otra estructura, no la tocamos.
        if "/" in resto:
            print(
                f"ADVERTENCIA: ruta no reconocida. "
                f"Registro={registro_id} Ruta={ruta}"
            )
            return

        ruta_nueva = f"registros/{empresa_id}/{resto}"

        # La URL es única, pero guardamos también los registros
        # asociados para poder auditar la migración.
        if ruta not in fotos:
            fotos[ruta] = {
                "ruta_actual": ruta,
                "ruta_nueva": ruta_nueva,
                "empresa_id": empresa_id,
                "registros": [],
            }

        fotos[ruta]["registros"].append(registro_id)

    for registro in registros:
        proyecto_id = registro.get("id_proyecto")

        if proyecto_id is None:
            continue

        empresa_id = empresa_por_proyecto.get(int(proyecto_id))

        if empresa_id is None:
            print(
                f"ADVERTENCIA: registro {registro.get('id')} "
                f"sin empresa para proyecto {proyecto_id}"
            )
            continue

        recorrer(
            registro.get("respuestas") or {},
            empresa_id,
            registro.get("id"),
        )

    return list(fotos.values())


def content_type_de(ruta):
    extension = ruta.lower().rsplit(".", 1)[-1]

    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }.get(extension, "application/octet-stream")


def main():
    fotos = obtener_fotos_a_migrar()

    print()
    print("==============================================")
    print("MIGRACION FOTOS BITACORA")
    print("==============================================")
    print(f"Modo DRY_RUN : {DRY_RUN}")
    print(f"Detectadas   : {len(fotos)}")
    print()

    if not fotos:
        print("No hay fotografías históricas pendientes.")
        return

    procesadas = 0
    copiadas = 0
    errores = 0

    for i, foto in enumerate(fotos, start=1):

        origen = foto["ruta_actual"]
        destino = foto["ruta_nueva"]

        print("----------------------------------------------")
        print(f"[{i}/{len(fotos)}]")
        print(f"Empresa  : {foto['empresa_id']}")
        print(f"Registros: {foto['registros']}")
        print(f"Origen   : {origen}")
        print(f"Destino  : {destino}")

        procesadas += 1

        # ================================================
        # MODO SIMULACIÓN
        # ================================================
        if DRY_RUN:
            print("DRY-RUN: no se realizó ninguna copia.")
            continue

        # ================================================
        # COPIA REAL
        # ================================================
        try:
            contenido = (
                supabase_client.storage
                .from_(BUCKET)
                .download(origen)
            )

            if not contenido:
                raise RuntimeError(
                    "El archivo de origen está vacío."
                )

            supabase_client.storage.from_(BUCKET).upload(
                destino,
                contenido,
                {
                    "content-type": content_type_de(destino),
                    "upsert": "false",
                },
            )

            copiadas += 1
            print("OK: fotografía copiada.")

        except Exception as e:
            errores += 1
            print(f"ERROR: {e}")

    print()
    print("==============================================")
    print("RESULTADO")
    print("==============================================")
    print(f"Modo DRY_RUN : {DRY_RUN}")
    print(f"Detectadas   : {len(fotos)}")
    print(f"Procesadas   : {procesadas}")
    print(f"Copiadas     : {copiadas}")
    print(f"Errores      : {errores}")

    if DRY_RUN:
        print()
        print("SIMULACIÓN FINALIZADA.")
        print("No se modificó Storage.")
        print("No se modificó la base de datos.")
    else:
        print()
        print("COPIA FINALIZADA.")
        print("Los archivos originales NO fueron eliminados.")
        print("La base de datos NO fue modificada.")

def verificar_fotos_migradas():
    fotos = obtener_fotos_a_migrar()

    print()
    print("==============================================")
    print("VERIFICACION DE FOTOS MIGRADAS")
    print("==============================================")
    print(f"Esperadas: {len(fotos)}")
    print()

    correctas = 0
    errores = 0

    for i, foto in enumerate(fotos, start=1):
        destino = foto["ruta_nueva"]

        try:
            contenido = (
                supabase_client.storage
                .from_(BUCKET)
                .download(destino)
            )

            if not contenido:
                raise RuntimeError("Archivo vacío")

            correctas += 1
            print(f"[{i}/{len(fotos)}] OK: {destino}")

        except Exception as e:
            errores += 1
            print(f"[{i}/{len(fotos)}] ERROR: {destino}")
            print(f"    {e}")

    print()
    print("==============================================")
    print("RESULTADO VERIFICACION")
    print("==============================================")
    print(f"Esperadas : {len(fotos)}")
    print(f"Correctas : {correctas}")
    print(f"Errores   : {errores}")


if __name__ == "__main__":
    verificar_fotos_migradas()
