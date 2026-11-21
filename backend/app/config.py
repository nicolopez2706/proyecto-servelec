"""Configuracion. Todo lo que cambia entre la laptop y la computadora de la empresa va en .env."""
import os
import secrets
from pathlib import Path

BASE =Path(__file__).resolve().parent.parent


def _load_env():
    f = BASE / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env()

# Valores de ejemplo que NUNCA se aceptan como clave secreta (son publicos: estan en el codigo y en .env.example).
_CLAVES_PUBLICAS = {"dev-no-usar-en-produccion", "cambiar-por-una-clave-larga-y-aleatoria", "cambiar-por-una-clave-larga"}
_clave_auto = {}


def _clave_en_archivo(ruta: Path) -> str:
    """Clave secreta generada sola y guardada en DATA_DIR\\secret.key (se crea una vez y se reusa)."""
    if ruta in _clave_auto:
        return _clave_auto[ruta]
    try:
        k = ruta.read_text(encoding="utf-8").strip()
    except OSError:
        k = ""
    if len(k) < 32:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        nueva = secrets.token_urlsafe(48)
        try:
            with open(ruta, "x", encoding="utf-8") as f:        # "x": si otro proceso la creo justo antes, no la pisa
                f.write(nueva)
            k = nueva
            try:
                os.chmod(ruta, 0o600)
            except OSError:
                pass
        except FileExistsError:
            k = ruta.read_text(encoding="utf-8").strip()
    _clave_auto[ruta] = k
    return k


class Settings:
    """Se lee en cada acceso para poder cambiarla en los tests."""

    @property
    def secret_key(self):
        """SECRET_KEY firma las sesiones. Si no esta en .env (o es uno de los valores de ejemplo), el portal genera una
        clave aleatoria propia y la guarda en DATA_DIR\\secret.key: nunca se usa una clave conocida."""
        k = os.getenv("SECRET_KEY", "").strip()
        if k and k not in _CLAVES_PUBLICAS:
            return k
        return _clave_en_archivo(self.data_dir / "secret.key")

    @property
    def secret_key_origin(self):
        """'env' si viene de .env, 'archivo' si la genero el portal."""
        k = os.getenv("SECRET_KEY", "").strip()
        return "env" if k and k not in _CLAVES_PUBLICAS else "archivo"

    @property
    def login_max_fails(self):
        """LOGIN_MAX_FAILS: intentos fallidos seguidos antes de bloquear el ingreso de ese correo (por defecto 5)."""
        try:
            return max(1, int(os.getenv("LOGIN_MAX_FAILS", "5")))
        except ValueError:
            return 5

    @property
    def login_lock_minutes(self):
        """LOGIN_LOCK_MINUTES: cuanto dura el bloqueo (por defecto 15 minutos)."""
        try:
            return max(1, int(os.getenv("LOGIN_LOCK_MINUTES", "15")))
        except ValueError:
            return 15

    @property
    def token_hours(self):
        return int(os.getenv("TOKEN_HOURS", "10"))

    @property
    def data_dir(self):
        return Path(os.getenv("DATA_DIR", BASE / "data"))

    @property
    def docs_root(self):
        return Path(os.getenv("DOCS_ROOT", BASE / "docs_prueba"))

    @property
    def read_only(self):
        """READ_ONLY=1: se puede simular y pedir, pero no aplicar cambios (para probar con datos reales sin riesgo)."""
        return os.getenv("READ_ONLY", "").strip().lower() in ("1", "true", "si", "sí", "on")

    @property
    def ignore_locks(self):
        """IGNORE_LOCKS=1: no tratar como 'abierto por otra persona' a los archivos con marca de bloqueo. Solo para
        copias de prueba, donde esas marcas quedaron de cuando se copiaron. En el servidor real dejarlo apagado."""
        return os.getenv("IGNORE_LOCKS", "").strip().lower() in ("1", "true", "si", "sí", "on")

    @property
    def magnus_export(self):
        """MAGNUS_EXPORT: planilla (.xlsx/.xls/.ods/.csv) con los artículos que exporta Magnus, o una carpeta donde se
        deja esa exportación (se usa la más nueva). Vacío = el control contra Magnus no está configurado."""
        return os.getenv("MAGNUS_EXPORT", "").strip().strip('"')

    @property
    def magnus_col_codigo(self):
        """MAGNUS_COL_CODIGO: nombre del encabezado (o letra de columna) del código en la exportación. Vacío = se detecta."""
        return os.getenv("MAGNUS_COL_CODIGO", "").strip()

    @property
    def magnus_col_desc(self):
        """MAGNUS_COL_DESC: ídem para la descripción."""
        return os.getenv("MAGNUS_COL_DESC", "").strip()

    @property
    def magnus_dir(self):
        return self.data_dir / "magnus"

    @property
    def allow_content_loss(self):
        """PERMITIR_PERDIDA_XLSX=1: guardar un .xlsx aunque se pierdan imagenes, formas, graficos o tablas dinamicas que
        la libreria no sabe conservar. Apagado por defecto: ese archivo queda sin tocar y se avisa en el pedido."""
        return os.getenv("PERMITIR_PERDIDA_XLSX", "").strip().lower() in ("1", "true", "si", "sí", "on")

    @property
    def scan_ttl(self):
        """SCAN_TTL=segundos  Cuanto se reusa el recorrido de las carpetas en buscar y en el catalogo (por defecto 30).
        0 = recorrer en cada consulta. Los cambios y las simulaciones siempre recorren de nuevo."""
        try:
            return max(0, int(os.getenv("SCAN_TTL", "30")))
        except ValueError:
            return 30

    @property
    def exclude_dirs(self):
        """EXCLUDE_DIRS=nombre;nombre2  Carpetas (por nombre) que no se recorren: software, documentacion HTML, etc."""
        return {x.strip().lower() for x in os.getenv("EXCLUDE_DIRS", "").split(";") if x.strip()}

    @property
    def extra_roots(self):
        """EXTRA_ROOTS=nombre=ruta;nombre2=ruta2  Carpetas fuera de DOCS_ROOT a las que apuntan accesos directos."""
        out = []
        for part in os.getenv("EXTRA_ROOTS", "").split(";"):
            if "=" in part:
                name, path = part.split("=", 1)
                if name.strip() and path.strip():
                    out.append((name.strip(), Path(path.strip())))
        return out

    @property
    def link_map(self):
        """LINK_MAP=origen=destino;...  Como se llama el servidor "visto desde el acceso directo" y donde esta en
        esta computadora. Ej: Z:\\Doc_Productos=C:\\proyecto-servelec\\docs_prueba"""
        out = []
        for part in os.getenv("LINK_MAP", "").split(";"):
            if "=" in part:
                a, b = part.split("=", 1)
                if a.strip() and b.strip():
                    out.append((a.strip(), b.strip()))
        return out

    @property
    def n8n_url(self):
        """N8N_WEBHOOK_URL: a donde el portal avisa lo que pasa (pedidos, codigos...). Vacio = no avisa."""
        return os.getenv("N8N_WEBHOOK_URL", "").strip()

    @property
    def n8n_secret(self):
        """N8N_WEBHOOK_SECRET: clave compartida; se envia en el encabezado X-Portal-Token para que n8n confirme que el aviso es del portal."""
        return os.getenv("N8N_WEBHOOK_SECRET", "").strip()

    @property
    def n8n_delay(self):
        try:
            return max(0.0, float(os.getenv("N8N_DELAY", "1.5")))
        except ValueError:
            return 1.5

    @property
    def portal_url(self):
        """PORTAL_URL: direccion del portal que se incluye en los avisos para armar enlaces. Sin barra final."""
        return os.getenv("PORTAL_URL", "http://127.0.0.1:8000").strip().rstrip("/")

    @property
    def db_path(self):
        return self.data_dir / "portal.db"

    @property
    def backup_dir(self):
        return self.data_dir / "respaldos"


settings = Settings()
