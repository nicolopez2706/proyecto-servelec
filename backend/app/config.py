"""Configuracion. Todo lo que cambia entre la laptop y la computadora de la empresa va en .env."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


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


class Settings:
    """Se lee en cada acceso para poder cambiarla en los tests."""

    @property
    def secret_key(self):
        return os.getenv("SECRET_KEY", "dev-no-usar-en-produccion")

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
    def db_path(self):
        return self.data_dir / "portal.db"

    @property
    def backup_dir(self):
        return self.data_dir / "respaldos"


settings = Settings()
