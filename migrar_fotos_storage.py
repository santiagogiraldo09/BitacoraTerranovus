import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
)

if not SUPABASE_URL:
    raise RuntimeError(
        "Falta SUPABASE_URL en las variables de entorno."
    )

if not SUPABASE_KEY:
    raise RuntimeError(
        "Falta SUPABASE_SERVICE_ROLE_KEY o SUPABASE_KEY "
        "en las variables de entorno."
    )

supabase_client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

BUCKET = "fotos-bitacora"


def obtener_fotos_a_migrar():
    """
    Obtiene todas las fotografías históricas referenciadas en
    respuestas_formulario y determina la empresa a partir del proyecto.
    """

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

    def recorrer(valor, empresa_id):
        if isinstance(valor, dict):
            for v in valor.values():
                recorrer(v, empresa_id)

        elif isinstance(valor, list):
            for v in valor:
                recorrer(v, empresa_id)

        elif isinstance(valor, str):
            marcador = "/fotos-bitacora/"

            if marcador not in valor:
                return

            ruta = valor.split(marcador, 1)[1]

            if not ruta.startswith("registros/"):
                return

            # Si ya está organizada por esta empresa,
            # no necesita migración.
            prefijo_empresa = f"registros/{empresa_id}/"

            if ruta.startswith(prefijo_empresa):
                return

            # Solo migramos el formato histórico:
            # registros/<archivo>
            resto = ruta[len("registros/"):]

            # Si contiene "/", ya tiene alguna estructura de carpetas.
            # Por seguridad no la tocamos automáticamente.
            if "/" in resto:
                print(f"⚠ Ruta no reconocida, omitida: {ruta}")
                return

            ruta_nueva = f"registros/{empresa_id}/{resto}"

            fotos[ruta] = {
                "ruta_actual": ruta,
                "ruta_nueva": ruta_nueva,
                "empresa_id": empresa_id,
            }

    for registro in registros:
        proyecto_id = registro.get("id_proyecto")

        if proyecto_id is None:
            continue

        empresa_id = empresa_por_proyecto.get(int(proyecto_id))

        if empresa_id is None:
            print(
                f"⚠ Registro {registro.get('id')} "
                f"sin empresa para proyecto {proyecto_id}"
            )
            continue

        recorrer(registro.get("respuestas") or {}, empresa_id)

    return list(fotos.values())


def main():
    fotos = obtener_fotos_a_migrar()

    print()
    print("==========================================")
    print(f"Fotografías detectadas para migrar: {len(fotos)}")
    print("==========================================")
    print()

    if not fotos:
        print("No hay fotografías pendientes.")
        return

    copiadas = 0
    errores = 0

    for i, foto in enumerate(fotos, start=1):
        origen = foto["ruta_actual"]
        destino = foto["ruta_nueva"]

        print(f"[{i}/{len(fotos)}]")
        print(f"Origen : {origen}")
        print(f"Destino: {destino}")

        try:
            # Descargar bytes del objeto original
            contenido = (
                supabase_client.storage
                .from_(BUCKET)
                .download(origen)
            )

            if not contenido:
                raise RuntimeError("El archivo origen está vacío.")

            # Determinar Content-Type por extensión
            extension = destino.lower().rsplit(".", 1)[-1]

            tipos = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "webp": "image/webp",
            }

            content_type = tipos.get(
                extension,
                "application/octet-stream"
            )

            # Crear COPIA en la nueva ruta.
            # No elimina el archivo original.
            supabase_client.storage.from_(BUCKET).upload(
                destino,
                contenido,
                {
                    "content-type": content_type,
                    "upsert": "false",
                }
            )

            copiadas += 1
            print("✓ Copiada correctamente")

        except Exception as e:
            errores += 1
            print(f"✗ ERROR: {e}")

        print()

    print("==========================================")
    print("RESULTADO")
    print("==========================================")
    print(f"Detectadas : {len(fotos)}")
    print(f"Copiadas   : {copiadas}")
    print(f"Errores    : {errores}")
    print()
    print("IMPORTANTE:")
    print("Los archivos originales NO fueron eliminados.")
    print("La base de datos NO fue modificada.")


if __name__ == "__main__":
    main()