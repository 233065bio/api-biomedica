from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from database import get_db_connection
from routers.auth import verificar_sesion, hash_password
from routers import auth, pacientes, usuarios, sesiones, interrupciones, senales, esp32
from admin.panel import ADMIN_HTML

app = FastAPI(title="AOS API")

# ── Registrar routers ──
app.include_router(auth.router)
app.include_router(pacientes.router)
app.include_router(usuarios.router)
app.include_router(sesiones.router)
app.include_router(interrupciones.router)
app.include_router(senales.router)
app.include_router(esp32.router)


# ─────────────────────────────────────────────
# STARTUP: crear tablas si no existen
# ─────────────────────────────────────────────
@app.on_event("startup")
def startup_event():
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()

        # ── Usuarios ──
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                usuario    VARCHAR(100) NOT NULL UNIQUE,
                contrasena VARCHAR(255) NOT NULL
            )
        """)

        # ── Pacientes — PRUEBAS ──
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pacientes_pruebas (
                id                       INT AUTO_INCREMENT PRIMARY KEY,
                nombre                   VARCHAR(200) NOT NULL,
                fecha_estudio            DATE,
                edad                     INT,
                sexo                     VARCHAR(20),
                enfermedad_cardiovascular VARCHAR(10),
                imc                      FLOAT,
                epworth                  INT
            )
        """)

        # ── Pacientes — VOLUNTARIOS ──
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pacientes_voluntarios (
                id                       INT AUTO_INCREMENT PRIMARY KEY,
                nombre                   VARCHAR(200) NOT NULL,
                fecha_estudio            DATE,
                edad                     INT,
                sexo                     VARCHAR(20),
                enfermedad_cardiovascular VARCHAR(10),
                imc                      FLOAT,
                epworth                  INT
            )
        """)

        # ── Sesiones (con columna grupo para saber a qué tabla apunta paciente_id) ──
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sesiones (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                paciente_id INT  NOT NULL,
                grupo       VARCHAR(20) NOT NULL DEFAULT 'pruebas',
                fecha       DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Horas de sesión ──
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS horas_sesion (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                sesion_id    INT NOT NULL,
                numero_hora  INT NOT NULL,
                hora_inicio  TIME,
                hora_fin     TIME,
                FOREIGN KEY (sesion_id) REFERENCES sesiones(id)
            )
        """)

        # ── Interrupciones (apneas) ──
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interrupciones (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                hora_sesion_id      INT NOT NULL,
                numero_interrupcion INT,
                hora_detectada      TIME,
                duracion_segundos   FLOAT,
                spo2                FLOAT,
                frecuencia_cardiaca FLOAT,
                anotacion           TEXT,
                FOREIGN KEY (hora_sesion_id) REFERENCES horas_sesion(id)
            )
        """)

        # ── Señales ESP32 ──
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS senales_esp32 (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                interrupcion_id INT NOT NULL,
                tipo_senal      VARCHAR(20),
                timestamp_ms    BIGINT,
                valor           FLOAT,
                FOREIGN KEY (interrupcion_id) REFERENCES interrupciones(id)
            )
        """)

        # ── Usuario admin por defecto ──
        cursor.execute("SELECT id FROM usuarios WHERE usuario = 'admin'")
        if not cursor.fetchone():
            hashed = hash_password("admin123")
            cursor.execute(
                "INSERT INTO usuarios (usuario, contrasena) VALUES ('admin', %s)",
                (hashed,)
            )

        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Tablas verificadas/creadas con éxito")

    except Exception as e:
        print(f"❌ Error al crear tablas: {e}")


# ─────────────────────────────────────────────
# PANEL ADMIN
# ─────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    if not verificar_sesion(request):
        return RedirectResponse(url="/login", status_code=302)
    return ADMIN_HTML


# ─────────────────────────────────────────────
# ROOT → redirect a admin
# ─────────────────────────────────────────────
@app.get("/")
def root():
    return RedirectResponse(url="/admin")
