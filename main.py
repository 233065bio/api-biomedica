from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import mysql.connector
import os
import bcrypt

app = FastAPI()

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
        port=int(os.getenv("MYSQL_PORT") or 3306)
    )

def verificar_sesion(request: Request):
    return request.cookies.get("session") == "ok"

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def check_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return plain == hashed

# ─────────────────────────────────────────────
# CREAR TABLAS AL INICIAR
# ─────────────────────────────────────────────
@app.on_event("startup")
def startup_event():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INT AUTO_INCREMENT PRIMARY KEY,
                usuario VARCHAR(100) NOT NULL UNIQUE,
                contrasena VARCHAR(255) NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pacientes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nombre VARCHAR(200) NOT NULL,
                fecha_estudio DATE,
                edad INT,
                sexo VARCHAR(20),
                enfermedad_cardiovascular VARCHAR(10),
                imc FLOAT,
                epworth INT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sesiones (
                id INT AUTO_INCREMENT PRIMARY KEY,
                paciente_id INT NOT NULL,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS horas_sesion (
                id INT AUTO_INCREMENT PRIMARY KEY,
                sesion_id INT NOT NULL,
                numero_hora INT NOT NULL,
                hora_inicio TIME,
                hora_fin TIME,
                FOREIGN KEY (sesion_id) REFERENCES sesiones(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interrupciones (
                id INT AUTO_INCREMENT PRIMARY KEY,
                hora_sesion_id INT NOT NULL,
                numero_interrupcion INT,
                hora_detectada TIME,
                duracion_segundos FLOAT,
                spo2 FLOAT,
                frecuencia_cardiaca FLOAT,
                anotacion TEXT,
                FOREIGN KEY (hora_sesion_id) REFERENCES horas_sesion(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS senales_esp32 (
                id INT AUTO_INCREMENT PRIMARY KEY,
                interrupcion_id INT NOT NULL,
                tipo_senal VARCHAR(20),
                timestamp_ms BIGINT,
                valor FLOAT,
                FOREIGN KEY (interrupcion_id) REFERENCES interrupciones(id) ON DELETE CASCADE
            )
        """)
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

# (Modelos y Helpers se mantienen igual que en tu archivo original...)
class SenalESP32(BaseModel):
    interrupcion_id: int
    tipo_senal: str
    timestamp_ms: int
    valor: float

class InterrupcionModel(BaseModel):
    hora_sesion_id: int
    numero_interrupcion: int
    hora_detectada: str
    duracion_segundos: float
    spo2: float
    frecuencia_cardiaca: float

class PacienteModel(BaseModel):
    nombre: str
    fecha_estudio: Optional[str] = None
    edad: Optional[int] = None
    sexo: Optional[str] = None
    enfermedad_cardiovascular: Optional[str] = None
    imc: Optional[float] = None
    epworth: Optional[int] = None

class UsuarioModel(BaseModel):
    usuario: str
    contrasena: str

class DatosESP32(BaseModel):
    paciente: str
    hora: str
    spo2: float
    ecg: float
    acce_z: float
    flujo: float
    no_apnea: int
    duracion: float

class LoginRequest(BaseModel):
    usuario: str
    contrasena: str

class AnotacionModel(BaseModel):
    anotacion: str

def timedelta_a_str(valor):
    if valor is None: return None
    if hasattr(valor, 'total_seconds'):
        total = int(valor.total_seconds())
        h, m, s = total // 3600, (total % 3600) // 60, total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    return str(valor)

# ─────────────────────────────────────────────
# LOGIN Y LOGOUT
# ─────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
def login_page(error: Optional[str] = None):
    error_html = '<p style="color:#D65C5C; text-align:center; font-size:13px; margin-bottom:12px;">Usuario o contraseña incorrectos</p>' if error else ''
    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8"><title>AOS — Login</title>
        <style>
            body {{ font-family: Arial, sans-serif; background: #EEF5FB; display: flex; justify-content: center; align-items: center; height: 100vh; margin:0; }}
            .card {{ background: white; border-radius: 10px; padding: 40px; width: 380px; box-shadow: 0 8px 32px rgba(44,74,90,0.12); border: 1px solid #D4E8F3; }}
            input {{ width: 100%; padding: 10px; border: 1px solid #D4E8F3; border-radius: 4px; margin-bottom: 16px; }}
            button {{ width: 100%; padding: 11px; background: #7AAFC5; color: white; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>⚙️ AOS Admin</h1>
            {error_html}
            <form method="post" action="/login">
                <input name="usuario" type="text" placeholder="Usuario" autofocus required>
                <input name="contrasena" type="password" placeholder="Contraseña" required>
                <button type="submit">Ingresar</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/login")
async def hacer_login(usuario: str = Form(...), contrasena: str = Form(...)):
    if usuario == ADMIN_USER and contrasena == ADMIN_PASS:
        response = RedirectResponse(url="/admin", status_code=302)
        response.set_cookie("session", "ok", httponly=True, samesite="lax")
        return response
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, contrasena FROM usuarios WHERE usuario = %s", (usuario,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if user and check_password(contrasena, user["contrasena"]):
            response = RedirectResponse(url="/admin", status_code=302)
            response.set_cookie("session", "ok", httponly=True, samesite="lax")
            return response
    except Exception: pass
    return RedirectResponse(url="/login?error=1", status_code=302)

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response

# ─────────────────────────────────────────────
# NUEVAS FUNCIONES DE ELIMINACIÓN (API)
# ─────────────────────────────────────────────

@app.delete("/sesiones/{sesion_id}")
def eliminar_sesion(sesion_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # El borrado en cascada (ON DELETE CASCADE) configurado en las tablas 
        # se encargará de borrar señales, interrupciones y horas automáticamente.
        cursor.execute("DELETE FROM sesiones WHERE id = %s", (sesion_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/pacientes/{paciente_id}/vaciar-datos")
def vaciar_datos_paciente(paciente_id: int, request: Request):
    """Borra todo el historial de monitoreo de un paciente sin eliminar al paciente."""
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Al borrar las sesiones, se borra todo lo asociado por CASCADE
        cursor.execute("DELETE FROM sesiones WHERE paciente_id = %s", (paciente_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "message": "Historial del paciente vaciado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# (Se mantienen el resto de endpoints como /pacientes, /usuarios, /senales...)
# [El código sigue con la misma estructura de tu main.py original...]

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    if not verificar_sesion(request):
        return RedirectResponse(url="/login", status_code=302)
    
    # Aquí es donde inyectamos los nuevos botones en el JS del Panel Admin
    # Busca en tu main.py la sección del <script> dentro de admin_panel y asegúrate de tener estas funciones:
    
    return """
    <!DOCTYPE html>
    <html>
    <head>...</head>
    <body>
        ...
        <script>
            // FUNCIONES DE ELIMINACIÓN AGREGADAS
            async function vaciarDatosPaciente(id) {
                if (confirm('¿Estás seguro de que deseas borrar TODO el historial de monitoreo de este paciente?')) {
                    const res = await fetch(`/pacientes/${id}/vaciar-datos`, { method: 'DELETE' });
                    if (res.ok) { 
                        mostrarToast('🗑️ Datos vaciados'); 
                        cargarPacientes(); 
                    }
                }
            }

            async function eliminarSesion(id) {
                if (confirm('¿Eliminar esta sesión y todos sus registros de señales?')) {
                    const res = await fetch(`/sesiones/${id}`, { method: 'DELETE' });
                    if (res.ok) {
                        mostrarToast('✅ Sesión eliminada');
                        // Lógica para refrescar la vista de sesiones si es necesario
                    }
                }
            }
            
            // ... resto de tus funciones como eliminarPaciente y eliminarUsuario
        </script>
    </body>
    </html>
    """
