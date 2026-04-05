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
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS horas_sesion (
                id INT AUTO_INCREMENT PRIMARY KEY,
                sesion_id INT NOT NULL,
                numero_hora INT NOT NULL,
                hora_inicio TIME,
                hora_fin TIME,
                FOREIGN KEY (sesion_id) REFERENCES sesiones(id)
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
                FOREIGN KEY (hora_sesion_id) REFERENCES horas_sesion(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS senales_esp32 (
                id INT AUTO_INCREMENT PRIMARY KEY,
                interrupcion_id INT NOT NULL,
                tipo_senal VARCHAR(20),
                timestamp_ms BIGINT,
                valor FLOAT,
                FOREIGN KEY (interrupcion_id) REFERENCES interrupciones(id)
            )
        """)
        cursor.execute("""
            INSERT IGNORE INTO usuarios (usuario, contrasena)
            VALUES ('admin', 'admin123')
        """)
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
# LOGIN ADMIN
# ─────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
def login_page():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AOS — Login</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: Arial, sans-serif; background: #EEF5FB;
                   display: flex; justify-content: center; align-items: center;
                   height: 100vh; }
            .card { background: white; border-radius: 10px; padding: 40px;
                    width: 380px; box-shadow: 0 8px 32px rgba(44,74,90,0.12);
                    border: 1px solid #D4E8F3; }
            h1 { font-family: 'Times New Roman', serif; color: #2C4A5A;
                 text-align: center; margin-bottom: 8px; font-size: 24px; }
            p { text-align: center; color: #5A7A8A; font-size: 13px; margin-bottom: 28px; }
            label { display: block; font-size: 12px; color: #5A7A8A;
                    font-weight: bold; margin-bottom: 4px; }
            input { width: 100%; padding: 10px 14px; border: 1px solid #D4E8F3;
                    border-radius: 4px; font-size: 14px; background: #EEF5FB;
                    color: #2C4A5A; margin-bottom: 16px; }
            button { width: 100%; padding: 11px; background: #7AAFC5; color: white;
                     border: none; border-radius: 4px; font-size: 15px;
                     font-weight: bold; cursor: pointer; }
            button:hover { background: #5B9AB5; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>⚙️ AOS Admin</h1>
            <p>Panel de Administración</p>
            <form method="post" action="/login">
                <label>Usuario</label>
                <input name="usuario" type="text" placeholder="usuario" autofocus>
                <label>Contraseña</label>
                <input name="contrasena" type="password" placeholder="••••••••">
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
        response.set_cookie("session", "ok", httponly=True)
        return response
    return RedirectResponse(url="/login?error=1", status_code=302)

class LoginRequest(BaseModel):
    usuario: str
    contrasena: str

@app.post("/api/login")
def api_login_json(data: LoginRequest):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, usuario FROM usuarios WHERE usuario=%s AND contrasena=%s",
            (data.usuario, data.contrasena)
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if user:
            return {"status": "ok", "usuario": user}
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response

# ─────────────────────────────────────────────
# ENDPOINTS ESP32
# ─────────────────────────────────────────────

@app.get("/datos-sensores")
def obtener_datos_sensores(request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
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

@app.post("/senales")
async def subir_senales(senales: List[SenalESP32]):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        sql = "INSERT INTO senales_esp32 (interrupcion_id, tipo_senal, timestamp_ms, valor) VALUES (%s, %s, %s, %s)"
        valores = [(s.interrupcion_id, s.tipo_senal, s.timestamp_ms, s.valor) for s in senales]
        cursor.executemany(sql, valores)
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/interrupciones")
async def crear_interrupcion(data: InterrupcionModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO interrupciones (hora_sesion_id, numero_interrupcion, hora_detectada, duracion_segundos, spo2, frecuencia_cardiaca)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (data.hora_sesion_id, data.numero_interrupcion, data.hora_detectada, data.duracion_segundos, data.spo2, data.frecuencia_cardiaca))
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        conn.close()
        return {"status": "success", "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ENDPOINTS PACIENTES
# ─────────────────────────────────────────────
@app.get("/pacientes")
def obtener_pacientes():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM pacientes ORDER BY fecha_estudio DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.post("/pacientes")
def crear_paciente(data: PacienteModel, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pacientes (nombre, fecha_estudio, edad, sexo, enfermedad_cardiovascular, imc, epworth) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                   (data.nombre, data.fecha_estudio, data.edad, data.sexo, data.enfermedad_cardiovascular, data.imc, data.epworth))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.put("/pacientes/{paciente_id}")
def editar_paciente(paciente_id: int, data: PacienteModel, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE pacientes SET nombre=%s, fecha_estudio=%s, edad=%s, sexo=%s, enfermedad_cardiovascular=%s, imc=%s, epworth=%s WHERE id=%s",
                   (data.nombre, data.fecha_estudio, data.edad, data.sexo, data.enfermedad_cardiovascular, data.imc, data.epworth, paciente_id))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.delete("/pacientes/{paciente_id}")
def eliminar_paciente(paciente_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pacientes WHERE id=%s", (paciente_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

# ─────────────────────────────────────────────
# ENDPOINTS USUARIOS
# ─────────────────────────────────────────────
@app.get("/usuarios")
def obtener_usuarios(request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, usuario FROM usuarios ORDER BY id")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.post("/usuarios")
def crear_usuario(data: UsuarioModel, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO usuarios (usuario, contrasena) VALUES (%s, %s)", (data.usuario, data.contrasena))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.delete("/usuarios/{usuario_id}")
def eliminar_usuario(usuario_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM usuarios WHERE id=%s", (usuario_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

# ─────────────────────────────────────────────
# PANEL ADMIN
# ─────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    if not verificar_sesion(request):
        return RedirectResponse(url="/login", status_code=302)
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AOS — Panel Admin</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: Arial, sans-serif; background: #FFFFFF; color: #2C4A5A; }
            .banner { background: #EEF5FB; padding: 14px 30px; border-bottom: 1px solid #D4E8F3; display: flex; align-items: center; justify-content: space-between; }
            .banner h1 { font-family: 'Times New Roman', serif; font-size: 22px; color: #2C4A5A; }
            .tabs { display: flex; background: #EEF5FB; border-bottom: 2px solid #D4E8F3; padding: 0 30px; }
            .tab { padding: 12px 24px; cursor: pointer; font-weight: bold; font-size: 13px; color: #5A7A8A; border-bottom: 3px solid transparent; }
            .tab.active { color: #7AAFC5; border-bottom: 3px solid #7AAFC5; }
            .content { padding: 24px 30px; }
            .section { display: none; }
            .section.active { display: block; }
            .toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
            .search { background: #EEF5FB; border: 1px solid #D4E8F3; padding: 8px 14px; width: 300px; border-radius: 4px; font-size: 13px; color: #2C4A5A; }
            .btn { padding: 8px 18px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: bold; }
            .btn-primary { background: #7AAFC5; color: white; }
            .btn-danger { background: #D65C5C; color: white; font-size: 11px; padding: 5px 10px; }
            .btn-edit { background: #EEF5FB; color: #2C4A5A; font-size: 11px; padding: 5px 10px; border: 1px solid #D4E8F3; }
            table { width: 100%; border-collapse: collapse; }
            th { background: #EEF5FB; color: #2C4A5A; padding: 10px; text-align: left; font-size: 13px; border-bottom: 2px solid #D4E8F3; }
            td { padding: 10px; border-bottom: 1px solid #D4E8F3; font-size: 13px; }
            .modal-bg { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.3); z-index: 100; justify-content: center; align-items: center; }
            .modal-bg.show { display: flex; }
            .modal { background: white; border-radius: 8px; padding: 28px; width: 460px; }
            .form-group { margin-bottom: 14px; }
            .form-group label { display: block; font-size: 12px; color: #5A7A8A; margin-bottom: 4px; font-weight: bold; }
            .form-group input, .form-group select { width: 100%; padding: 8px 12px; border: 1px solid #D4E8F3; border-radius: 4px; font-size: 13px; background: #EEF5FB; }
            .badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }
            .badge-ok { background: #EEF8F2; color: #2E7D52; }
            .badge-warn { background: #FFF8EC; color: #B07020; }
            .badge-crit { background: #FFF0EE; color: #A02020; }
            .toast { position: fixed; bottom: 30px; right: 30px; background: #2C4A5A; color: white; padding: 12px 24px; border-radius: 6px; display: none; }
            .toast.show { display: block; }
        </style>
    </head>
    <body>
        <div class="banner">
            <h1>⚙️ AOS — Panel de Administración</h1>
            <a href="/logout" style="color:#7AAFC5; text-decoration:none; font-size:13px;">Cerrar sesión</a>
        </div>
        <div class="tabs">
            <div class="tab active" onclick="cambiarTab('pacientes')">👥 Pacientes</div>
            <div class="tab" onclick="cambiarTab('usuarios')">🔑 Usuarios</div>
            <div class="tab" onclick="cambiarTab('monitoreo')">📊 Monitoreo ESP32</div>
        </div>
        <div class="content">
            <div id="sec-pacientes" class="section active">
                <div class="toolbar">
                    <input class="search" id="buscar-pac" placeholder="🔍 Buscar paciente..." oninput="filtrarPacientes()">
                    <button class="btn btn-primary" onclick="abrirModalPaciente()">+ Nuevo paciente</button>
                </div>
                <table>
                    <thead>
                        <tr><th>Nombre</th><th>Fecha estudio</th><th>Edad</th><th>Sexo</th><th>IMC</th><th>EPWORTH</th><th>Acciones</th></tr>
                    </thead>
                    <tbody id="tbody-pacientes"></tbody>
                </table>
            </div>

            <div id="sec-usuarios" class="section">
                <div class="toolbar">
                    <span style="font-size:13px; color:#5A7A8A;">Gestión de usuarios</span>
                    <button class="btn btn-primary" onclick="abrirModalUsuario()">+ Nuevo usuario</button>
                </div>
                <table>
                    <thead><tr><th>ID</th><th>Usuario</th><th>Acciones</th></tr></thead>
                    <tbody id="tbody-usuarios"></tbody>
                </table>
            </div>

            <div id="sec-monitoreo" class="section">
                <div class="toolbar">
                    <span style="font-size:13px; color:#5A7A8A;">Registros históricos enviados por ESP32 (Solo lectura)</span>
                    <button class="btn btn-primary" onclick="cargarMonitoreo()">🔄 Actualizar</button>
                </div>
                <table>
                    <thead>
                        <tr><th>Paciente</th><th>Hora</th><th>SpO2</th><th>ECG</th><th>Acce Z</th><th>Flujo</th><th>N° Apnea</th><th>Duración</th></tr>
                    </thead>
                    <tbody id="tbody-monitoreo"></tbody>
                </table>
            </div>
        </div>

        <div class="modal-bg" id="modal-paciente">
            <div class="modal">
                <h2 id="modal-pac-titulo">Paciente</h2>
                <input type="hidden" id="pac-id">
                <div class="form-group"><label>Nombre completo</label><input id="pac-nombre"></div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                    <div class="form-group"><label>Fecha</label><input id="pac-fecha" type="date"></div>
                    <div class="form-group"><label>Edad</label><input id="pac-edad" type="number"></div>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                    <div class="form-group"><label>Sexo</label><select id="pac-sexo"><option value="M">M</option><option value="F">F</option></select></div>
                    <div class="form-group"><label>Cardio</label><select id="pac-cardio"><option value="Si">Si</option><option value="No">No</option></select></div>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                    <div class="form-group"><label>IMC</label><input id="pac-imc" type="number" step="0.1"></div>
                    <div class="form-group"><label>EPWORTH</label><input id="pac-epworth" type="number"></div>
                </div>
                <div style="text-align:right; margin-top:10px;">
                    <button class="btn" style="background:#EEE; color:#333;" onclick="cerrarModals()">Cancelar</button>
                    <button class="btn btn-primary" onclick="guardarPaciente()">Guardar</button>
                </div>
            </div>
        </div>

        <div class="modal-bg" id="modal-usuario">
            <div class="modal">
                <h2>Nuevo Usuario</h2>
                <div class="form-group"><label>Usuario</label><input id="usr-nombre"></div>
                <div class="form-group"><label>Contraseña</label><input id="usr-pass" type="password"></div>
                <div style="text-align:right;"><button class="btn" onclick="cerrarModals()">Cancelar</button><button class="btn btn-primary" onclick="guardarUsuario()">Guardar</button></div>
            </div>
        </div>

        <div class="toast" id="toast"></div>

        <script>
            let pacientes = [];

            function mostrarToast(msg) {
                const t = document.getElementById('toast');
                t.innerText = msg; t.classList.add('show');
                setTimeout(() => t.classList.remove('show'), 2500);
            }

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
                pacientes = await res.json();
                mostrarPacientes(pacientes);
            }

            function mostrarPacientes(datos) {
                const tb = document.getElementById('tbody-pacientes');
                tb.innerHTML = datos.map(p => `
                    <tr>
                        <td><strong>${p.nombre}</strong></td>
                        <td>${p.fecha_estudio || '--'}</td>
                        <td>${p.edad || '--'}</td>
                        <td>${p.sexo || '--'}</td>
                        <td><span class="badge ${p.imc >= 30 ? 'badge-crit' : 'badge-ok'}">${p.imc || '--'}</span></td>
                        <td><span class="badge ${p.epworth >= 10 ? 'badge-warn' : 'badge-ok'}">${p.epworth || '--'}</span></td>
                        <td>
                            <button class="btn btn-edit" onclick='editarPaciente(${JSON.stringify(p)})'>✏️</button>
                            <button class="btn btn-danger" onclick="eliminarPaciente(${p.id})">🗑️</button>
                        </td>
                    </tr>
                `).join('');
            }

            function filtrarPacientes() {
                const q = document.getElementById('buscar-pac').value.toLowerCase();
                mostrarPacientes(pacientes.filter(p => p.nombre.toLowerCase().includes(q)));
            }

            async function cargarMonitoreo() {
                const res = await fetch('/datos-sensores');
                const datos = await res.json();
                document.getElementById('tbody-monitoreo').innerHTML = datos.map(d => `
                    <tr>
                        <td><strong>${d.paciente_nombre}</strong></td>
                        <td>${d.hora_detectada}</td>
                        <td><span class="badge ${d.spo2 < 90 ? 'badge-crit' : 'badge-ok'}">${d.spo2}%</span></td>
                        <td>${d.ecg}</td>
                        <td>${d.acce_z || 0}</td>
                        <td>${d.flujo || 0}</td>
                        <td>${d.numero_apnea}</td>
                        <td>${d.duracion_apnea}s</td>
                    </tr>
                `).join('');
            }

            async function cargarUsuarios() {
                const res = await fetch('/usuarios');
                const data = await res.json();
                document.getElementById('tbody-usuarios').innerHTML = data.map(u => `
                    <tr><td>${u.id}</td><td>${u.usuario}</td><td><button class="btn btn-danger" onclick="eliminarUsuario(${u.id})">🗑️</button></td></tr>
                `).join('');
            }

            function abrirModalPaciente() { document.getElementById('pac-id').value=''; document.getElementById('modal-paciente').classList.add('show'); }
            function abrirModalUsuario() { document.getElementById('modal-usuario').classList.add('show'); }
            function cerrarModals() { document.querySelectorAll('.modal-bg').forEach(m => m.classList.remove('show')); }

            function editarPaciente(p) {
                document.getElementById('pac-id').value = p.id;
                document.getElementById('pac-nombre').value = p.nombre;
                document.getElementById('pac-fecha').value = p.fecha_estudio;
                document.getElementById('pac-edad').value = p.edad;
                document.getElementById('pac-sexo').value = p.sexo;
                document.getElementById('pac-cardio').value = p.enfermedad_cardiovascular;
                document.getElementById('pac-imc').value = p.imc;
                document.getElementById('pac-epworth').value = p.epworth;
                document.getElementById('modal-paciente').classList.add('show');
            }

            async function guardarPaciente() {
                const id = document.getElementById('pac-id').value;
                const body = {
                    nombre: document.getElementById('pac-nombre').value,
                    fecha_estudio: document.getElementById('pac-fecha').value || null,
                    edad: parseInt(document.getElementById('pac-edad').value) || null,
                    sexo: document.getElementById('pac-sexo').value,
                    enfermedad_cardiovascular: document.getElementById('pac-cardio').value,
                    imc: parseFloat(document.getElementById('pac-imc').value) || null,
                    epworth: parseInt(document.getElementById('pac-epworth').value) || null
                };
                await fetch(id ? '/pacientes/'+id : '/pacientes', {
                    method: id ? 'PUT' : 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                cerrarModals(); cargarPacientes(); mostrarToast('Hecho');
            }

            async function eliminarPaciente(id) { if(confirm('¿Seguro?')) { await fetch('/pacientes/'+id, {method:'DELETE'}); cargarPacientes(); } }
            async function eliminarUsuario(id) { if(confirm('¿Seguro?')) { await fetch('/usuarios/'+id, {method:'DELETE'}); cargarUsuarios(); } }
            async function guardarUsuario() {
                await fetch('/usuarios', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({usuario: document.getElementById('usr-nombre').value, contrasena: document.getElementById('usr-pass').value})
                });
                cerrarModals(); cargarUsuarios();
            }

            window.onload = cargarPacientes;
        </script>
    </body>
    </html>
    """
