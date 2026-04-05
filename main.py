from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import mysql.connector
import os

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
        cursor.execute("INSERT IGNORE INTO usuarios (usuario, contrasena) VALUES ('admin', 'admin123')")
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Tablas verificadas/creadas con éxito")
    except Exception as e:
        print(f"❌ Error al crear tablas: {e}")

# ─────────────────────────────────────────────
# MODELOS
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
# ENDPOINTS LOGICA
# ─────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page():
    return """
    <html><body style="font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; background:#EEF5FB;">
    <form method="post" style="background:white; padding:30px; border-radius:8px; box-shadow:0 4px 10px rgba(0,0,0,0.1);">
        <h2>AOS Admin Login</h2>
        <input name="usuario" placeholder="Usuario" style="display:block; width:100%; margin-bottom:10px; padding:8px;">
        <input name="contrasena" type="password" placeholder="Contraseña" style="display:block; width:100%; margin-bottom:10px; padding:8px;">
        <button type="submit" style="width:100%; padding:10px; background:#7AAFC5; color:white; border:none; cursor:pointer;">Entrar</button>
    </form></body></html>
    """

@app.post("/login")
async def hacer_login(usuario: str = Form(...), contrasena: str = Form(...)):
    if usuario == ADMIN_USER and contrasena == ADMIN_PASS:
        response = RedirectResponse(url="/admin", status_code=302)
        response.set_cookie("session", "ok", httponly=True)
        return response
    return RedirectResponse(url="/login", status_code=302)

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response

# ─────────────────────────────────────────────
# ENDPOINTS DATOS (ESP32 y CONSULTA)
# ─────────────────────────────────────────────

@app.get("/datos-sensores")
def obtener_datos_sensores(request: Request):
    if not verificar_sesion(request): raise HTTPException(status_code=401)
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT 
                p.nombre AS paciente_nombre,
                i.hora_detectada, i.spo2, i.frecuencia_cardiaca AS ecg,
                i.duracion_segundos AS duracion_apnea, i.numero_interrupcion AS numero_apnea,
                (SELECT valor FROM senales_esp32 WHERE interrupcion_id = i.id AND tipo_senal = 'acce_z' LIMIT 1) AS acce_z,
                (SELECT valor FROM senales_esp32 WHERE interrupcion_id = i.id AND tipo_senal = 'flujo' LIMIT 1) AS flujo
            FROM interrupciones i
            JOIN horas_sesion hs ON i.hora_sesion_id = hs.id
            JOIN sesiones s ON hs.sesion_id = s.id
            JOIN pacientes p ON s.paciente_id = p.id
            ORDER BY i.id DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        return {"error": str(e)}

@app.post("/interrupciones")
async def crear_interrupcion(data: InterrupcionModel):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO interrupciones (hora_sesion_id, numero_interrupcion, hora_detectada, duracion_segundos, spo2, frecuencia_cardiaca) VALUES (%s, %s, %s, %s, %s, %s)", 
                   (data.hora_sesion_id, data.numero_interrupcion, data.hora_detectada, data.duracion_segundos, data.spo2, data.frecuencia_cardiaca))
    conn.commit()
    new_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return {"status": "success", "id": new_id}

@app.post("/senales")
async def subir_senales(senales: List[SenalESP32]):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.executemany("INSERT INTO senales_esp32 (interrupcion_id, tipo_senal, timestamp_ms, valor) VALUES (%s, %s, %s, %s)",
                       [(s.interrupcion_id, s.tipo_senal, s.timestamp_ms, s.valor) for s in senales])
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.get("/pacientes")
def obtener_pacientes():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM pacientes ORDER BY id DESC")
    res = cursor.fetchall()
    cursor.close()
    conn.close()
    return res

@app.post("/pacientes")
def crear_paciente(data: PacienteModel, request: Request):
    if not verificar_sesion(request): raise HTTPException(status_code=401)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pacientes (nombre, fecha_estudio, edad, sexo, enfermedad_cardiovascular, imc, epworth) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                   (data.nombre, data.fecha_estudio, data.edad, data.sexo, data.enfermedad_cardiovascular, data.imc, data.epworth))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.get("/usuarios")
def obtener_usuarios(request: Request):
    if not verificar_sesion(request): raise HTTPException(status_code=401)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, usuario FROM usuarios")
    res = cursor.fetchall()
    cursor.close()
    conn.close()
    return res

# ─────────────────────────────────────────────
# PANEL ADMIN (HTML)
# ─────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    if not verificar_sesion(request): return RedirectResponse(url="/login")
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AOS — Panel Admin</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: Arial, sans-serif; background: #FFFFFF; color: #2C4A5A; }
            .banner { background: #EEF5FB; padding: 14px 30px; border-bottom: 1px solid #D4E8F3; display: flex; justify-content: space-between; align-items: center; }
            .tabs { display: flex; background: #EEF5FB; border-bottom: 2px solid #D4E8F3; padding: 0 30px; }
            .tab { padding: 12px 24px; cursor: pointer; font-weight: bold; font-size: 13px; color: #5A7A8A; }
            .tab.active { color: #7AAFC5; border-bottom: 3px solid #7AAFC5; }
            .content { padding: 24px 30px; }
            .section { display: none; }
            .section.active { display: block; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th { background: #EEF5FB; padding: 10px; text-align: left; font-size: 13px; border-bottom: 2px solid #D4E8F3; }
            td { padding: 10px; border-bottom: 1px solid #D4E8F3; font-size: 13px; }
            .badge { padding: 3px 8px; border-radius: 10px; font-size: 11px; font-weight: bold; }
            .badge-crit { background: #FEE2E2; color: #DC2626; }
            .badge-ok { background: #DCFCE7; color: #16A34A; }
            .btn { padding: 6px 12px; border-radius: 4px; border: none; cursor: pointer; font-weight: bold; }
            .btn-primary { background: #7AAFC5; color: white; }
        </style>
    </head>
    <body>
        <div class="banner">
            <h1>⚙️ AOS Admin</h1>
            <div><a href="/logout" style="font-size:12px; color:#7AAFC5;">Cerrar Sesión</a></div>
        </div>
        <div class="tabs">
            <div class="tab active" onclick="cambiarTab('pacientes')">👥 Pacientes</div>
            <div class="tab" onclick="cambiarTab('usuarios')">🔑 Usuarios</div>
            <div class="tab" onclick="cambiarTab('monitoreo')">📊 Monitoreo ESP32</div>
        </div>
        <div class="content">
            <div id="sec-pacientes" class="section active">
                <button class="btn btn-primary" onclick="alert('Funcionalidad de agregar en desarrollo...')">+ Nuevo Paciente</button>
                <table id="tabla-pacientes">
                    <thead><tr><th>Nombre</th><th>Edad</th><th>Sexo</th><th>IMC</th></tr></thead>
                    <tbody id="tbody-pacientes"></tbody>
                </table>
            </div>
            
            <div id="sec-usuarios" class="section">
                <table id="tabla-usuarios">
                    <thead><tr><th>ID</th><th>Usuario</th></tr></thead>
                    <tbody id="tbody-usuarios"></tbody>
                </table>
            </div>

            <div id="sec-monitoreo" class="section">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <p style="font-size:12px; color:gray;">Datos recibidos desde la ESP32 (No modificables)</p>
                    <button class="btn btn-primary" onclick="cargarMonitoreo()">🔄 Actualizar</button>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Paciente</th><th>Hora</th><th>SpO2</th><th>ECG</th><th>Acce Z</th><th>Flujo</th><th>Apnea N°</th><th>Duración</th>
                        </tr>
                    </thead>
                    <tbody id="tbody-monitoreo"></tbody>
                </table>
            </div>
        </div>

        <script>
            function cambiarTab(tab) {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
                event.currentTarget.classList.add('active');
                document.getElementById('sec-' + tab).classList.add('active');
                if(tab === 'pacientes') cargarPacientes();
                if(tab === 'usuarios') cargarUsuarios();
                if(tab === 'monitoreo') cargarMonitoreo();
            }

            async function cargarPacientes() {
                const res = await fetch('/pacientes');
                const data = await res.json();
                document.getElementById('tbody-pacientes').innerHTML = data.map(p => `
                    <tr><td>${p.nombre}</td><td>${p.edad}</td><td>${p.sexo}</td><td>${p.imc}</td></tr>
                `).join('');
            }

            async function cargarUsuarios() {
                const res = await fetch('/usuarios');
                const data = await res.json();
                document.getElementById('tbody-usuarios').innerHTML = data.map(u => `
                    <tr><td>${u.id}</td><td>${u.usuario}</td></tr>
                `).join('');
            }

            async function cargarMonitoreo() {
                const res = await fetch('/datos-sensores');
                const data = await res.json();
                const tb = document.getElementById('tbody-monitoreo');
                tb.innerHTML = data.map(d => `
                    <tr>
                        <td><strong>${d.paciente_nombre}</strong></td>
                        <td>${d.hora_detectada}</td>
                        <td><span class="badge ${d.spo2 < 90 ? 'badge-crit' : 'badge-ok'}">${d.spo2}%</span></td>
                        <td>${d.ecg} bpm</td>
                        <td>${d.acce_z || 0}</td>
                        <td>${d.flujo || 0}</td>
                        <td>${d.numero_apnea}</td>
                        <td>${d.duracion_apnea}s</td>
                    </tr>
                `).join('');
            }

            window.onload = cargarPacientes;
        </script>
    </body>
    </html>
    """
