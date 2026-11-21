"""Seguridad del ingreso: clave secreta propia, claves iniciales temporales con cambio obligatorio,
restablecer clave, bloqueo por intentos fallidos y bases viejas."""
import sqlite3
import sys
from pathlib import Path

import pytest
from test_core import env  # noqa: F401
from app import db, router, services
from app.config import settings
from app.security import make_token, read_token

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _post(path, token=None, body=None):
    return router.dispatch("POST", path, {}, body or {}, token)


def _login(email, pw):
    return router.dispatch("POST", "/api/login", {}, {"email": email, "password": pw}, None)


# ---------- clave secreta ----------
def test_sin_secret_key_el_portal_genera_una_propia_y_no_la_publica(env, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    k = settings.secret_key
    assert len(k) >= 32 and k != "dev-no-usar-en-produccion"
    assert settings.secret_key == k                                   # estable: no cambia entre pedidos
    assert (settings.data_dir / "secret.key").read_text().strip() == k  # y queda guardada para el proximo arranque
    assert settings.secret_key_origin == "archivo"
    assert read_token(make_token("ing@x")) == "ing@x"


@pytest.mark.parametrize("publica", ["dev-no-usar-en-produccion", "cambiar-por-una-clave-larga-y-aleatoria"])
def test_los_valores_de_ejemplo_no_se_aceptan_como_clave(env, monkeypatch, publica):
    monkeypatch.setenv("SECRET_KEY", publica)
    assert settings.secret_key != publica and settings.secret_key_origin == "archivo"
    # un token firmado con la clave publica conocida no sirve
    import jwt, datetime as dt
    falso = jwt.encode({"sub": "sis@x", "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)}, publica, algorithm="HS256")
    assert read_token(falso) is None


def test_secret_key_propia_se_usa(env, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "una-clave-propia-bien-larga-0123456789abcdef")
    assert settings.secret_key == "una-clave-propia-bien-larga-0123456789abcdef" and settings.secret_key_origin == "env"


# ---------- alta, clave temporal y cambio obligatorio ----------
def test_usuario_nuevo_recibe_clave_temporal_y_debe_cambiarla(env):
    services_, u, *_ = env
    r = services.create_user(u["sis@x"], "Nueva@Servelec.com.ar", "Nueva Persona", "Abastecimiento", "")
    temp = r["temp"]
    assert temp and len(temp) == 10
    st, d = _login("nueva@servelec.com.ar", temp)
    assert st == 200 and d["user"]["must_change"] is True
    tok = d["token"]
    # solo puede ver quien es y cambiar la clave: todo lo demas queda bloqueado
    assert router.dispatch("GET", "/api/me", {}, {}, tok)[0] == 200
    assert router.dispatch("GET", "/api/orders", {}, {}, tok)[0] == 403
    assert router.dispatch("GET", "/api/codes", {}, {}, tok)[0] == 403
    # no acepta claves flojas ni la misma temporal
    for mala in ("cambiar123", "corta", "nueva", temp):
        assert _post("/api/me/password", tok, {"current": temp, "new": mala})[0] == 400
    st, _ = _post("/api/me/password", tok, {"current": temp, "new": "Una-clave-buena-2026"})
    assert st == 200
    assert router.dispatch("GET", "/api/orders", {}, {}, tok)[0] == 200          # ya puede trabajar
    assert _login("nueva@servelec.com.ar", temp)[0] == 401
    assert _login("nueva@servelec.com.ar", "Una-clave-buena-2026")[1]["user"]["must_change"] is False


def test_alta_con_clave_elegida_tambien_obliga_a_cambiar_y_valida(env):
    services_, u, *_ = env
    with pytest.raises(ValueError):
        services.create_user(u["sis@x"], "x@servelec.com.ar", "X", "Gerencia", "cambiar123")
    r = services.create_user(u["sis@x"], "y@servelec.com.ar", "Y", "Gerencia", "Provisoria-77")
    assert r["temp"] is None                                     # no se repite en pantalla una clave que ya conoce quien la puso
    assert _login("y@servelec.com.ar", "Provisoria-77")[1]["user"]["must_change"] is True


def test_las_claves_temporales_son_distintas(env):
    assert len({services.clave_temporal() for _ in range(50)}) == 50


# ---------- restablecer ----------
def test_restablecer_clave_solo_sistemas_y_sin_dejar_rastro_de_la_clave(env):
    services_, u, *_ = env
    from app.permissions import PermissionDenied
    with pytest.raises(PermissionDenied):
        services.reset_password(u["ger@x"], "ab@x")
    with pytest.raises(LookupError):
        services.reset_password(u["sis@x"], "nadie@x")
    r = services.reset_password(u["sis@x"], "ab@x")
    assert _login("ab@x", "clave1234")[0] == 401                  # la anterior ya no sirve
    st, d = _login("ab@x", r["temp"])
    assert st == 200 and d["user"]["must_change"] is True
    con = db.connect()
    texto = " ".join(str(tuple(x)) for x in con.execute("SELECT * FROM audit"))
    con.close()
    assert r["temp"] not in texto and "Restableció la clave de" in texto
    # por la API: solo Sistemas
    sis = _login("sis@x", "clave1234")[1]["token"]
    assert _post("/api/users/ab@x/reset", _login("ger@x", "clave1234")[1]["token"])[0] == 403
    st, d = _post("/api/users/ab@x/reset", sis)
    assert st == 200 and len(d["temp"]) == 10


# ---------- bloqueo por intentos ----------
def test_bloqueo_tras_intentos_fallidos_y_se_levanta_solo(env, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILS", "3")
    monkeypatch.setenv("LOGIN_LOCK_MINUTES", "10")
    for _ in range(3):
        assert _login("ing@x", "mala")[0] == 401
    st, d = _login("ing@x", "clave1234")                          # ni con la clave correcta mientras dura el bloqueo
    assert st == 429 and "minutos" in d["detail"]
    assert _login("ab@x", "clave1234")[0] == 200                  # otras personas no se ven afectadas
    # pasan 11 minutos: se levanta solo
    con = db.connect()
    con.execute("UPDATE login_fail SET at=?", ("2000-01-01T00:00:00",))
    con.commit(); con.close()
    assert _login("ing@x", "clave1234")[0] == 200


def test_ingreso_correcto_borra_los_fallos_anteriores(env, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILS", "3")
    for _ in range(2):
        assert _login("ing@x", "mala")[0] == 401
    assert _login("ing@x", "clave1234")[0] == 200
    for _ in range(2):
        assert _login("ing@x", "mala")[0] == 401
    assert _login("ing@x", "clave1234")[0] == 200                  # los 2 + 2 no se suman: el correcto reinicio la cuenta


def test_correos_inexistentes_tambien_cuentan_y_el_bloqueo_queda_auditado(env, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILS", "2")
    for _ in range(2):
        assert _login("fantasma@x", "mala")[0] == 401
    assert _login("fantasma@x", "mala")[0] == 429
    con = db.connect()
    bloqueos = con.execute("SELECT COUNT(*) FROM audit WHERE action='Ingreso bloqueado por intentos fallidos'").fetchone()[0]
    con.close()
    assert bloqueos == 1                                          # una sola vez por bloqueo, no por cada intento


def test_restablecer_desbloquea(env, monkeypatch):
    services_, u, *_ = env
    monkeypatch.setenv("LOGIN_MAX_FAILS", "2")
    for _ in range(2):
        _login("ab@x", "mala")
    assert _login("ab@x", "clave1234")[0] == 429
    t = services.reset_password(u["sis@x"], "ab@x")["temp"]
    assert _login("ab@x", t)[0] == 200


# ---------- bases viejas y seed ----------
def test_base_vieja_sin_must_change_se_actualiza_sin_perder_usuarios(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    con = sqlite3.connect(tmp_path / "data" / "portal.db")
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)")
    con.execute("INSERT INTO users(email,name,role,pw_hash) VALUES('v@x','Vieja','Gerencia','scrypt$00$00')")
    con.commit(); con.close()
    db.init_db(); db.init_db()                                    # dos veces: no debe fallar
    con = db.connect()
    r = con.execute("SELECT * FROM users").fetchone()
    con.close()
    assert r["email"] == "v@x" and r["must_change"] == 0


def test_seed_da_una_clave_distinta_a_cada_usuario(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    import seed
    creados = seed.sembrar()
    assert len(creados) == len(seed.USERS) and len({pw for _, _, pw in creados}) == len(creados)
    assert all(pw != "cambiar123" for *_, pw in creados)
    assert seed.sembrar() == []                                    # volver a correrlo no cambia las claves de nadie
    st, d = _login(creados[0][0], creados[0][2])
    assert st == 200 and d["user"]["must_change"] is True
    # y se puede forzar el cambio a quienes ya existian (bases con la clave comun de antes)
    con = db.connect(); con.execute("UPDATE users SET must_change=0"); con.commit(); con.close()
    assert seed.exigir_cambio() == len(seed.USERS)


def test_reset_clave_por_consola(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    import seed, reset_clave
    seed.sembrar()
    nueva = reset_clave.restablecer("n.lopez@servelec.com.ar")
    st, d = _login("n.lopez@servelec.com.ar", nueva)
    assert st == 200 and d["user"]["must_change"] is True
    with pytest.raises(SystemExit):
        reset_clave.restablecer("nadie@servelec.com.ar")
