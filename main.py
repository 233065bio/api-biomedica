from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import List, Optional
import mysql.connector
import os
import secrets

app = FastAPI()
security = HTTPBasic()

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

def verificar_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    ok_pass = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="No autorizado",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username

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
# ENDPOINTS ESP32
# ─────────────────────────────────────────────
@app.post("/senales")
async def subir_senales(senales: List[SenalESP32]):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        sql = """INSERT INTO senales_esp32
                 (interrupcion_id, tipo_senal, timestamp_ms, valor)
                 VALUES (%s, %s, %s, %s)"""
        valores = [(s.interrupcion_id, s.tipo_senal, s.timestamp_ms, s.valor)
                   for s in senales]
        cursor.executemany(sql, valores)
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "registros": len(senales)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/interrupciones")
async def crear_interrupcion(data: InterrupcionModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO interrupciones
            (hora_sesion_id, numero_interrupcion, hora_detectada,
             duracion_segundos, spo2, frecuencia_cardiaca)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (data.hora_sesion_id, data.numero_interrupcion, data.hora_detectada,
              data.duracion_segundos, data.spo2, data.frecuencia_cardiaca))
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        conn.close()
        return {"status": "success", "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ENDPOINTS PACIENTES (ADMIN)
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
def crear_paciente(data: PacienteModel, admin=Depends(verificar_admin)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pacientes
            (nombre, fecha_estudio, edad, sexo, enfermedad_cardiovascular, imc, epworth)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (data.nombre, data.fecha_estudio, data.edad, data.sexo,
              data.enfermedad_cardiovascular, data.imc, data.epworth))
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        conn.close()
        return {"status": "success", "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/pacientes/{paciente_id}")
def editar_paciente(paciente_id: int, data: PacienteModel, admin=Depends(verificar_admin)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pacientes SET nombre=%s, fecha_estudio=%s, edad=%s,
            sexo=%s, enfermedad_cardiovascular=%s, imc=%s, epworth=%s
            WHERE id=%s
        """, (data.nombre, data.fecha_estudio, data.edad, data.sexo,
              data.enfermedad_cardiovascular, data.imc, data.epworth, paciente_id))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/pacientes/{paciente_id}")
def eliminar_paciente(paciente_id: int, admin=Depends(verificar_admin)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pacientes WHERE id=%s", (paciente_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ENDPOINTS USUARIOS (ADMIN)
# ─────────────────────────────────────────────
@app.get("/usuarios")
def obtener_usuarios(admin=Depends(verificar_admin)):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, usuario FROM usuarios ORDER BY id")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.post("/usuarios")
def crear_usuario(data: UsuarioModel, admin=Depends(verificar_admin)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO usuarios (usuario, contrasena) VALUES (%s, %s)",
                       (data.usuario, data.contrasena))
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        conn.close()
        return {"status": "success", "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/usuarios/{usuario_id}")
def editar_usuario(usuario_id: int, data: UsuarioModel, admin=Depends(verificar_admin)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE usuarios SET usuario=%s, contrasena=%s WHERE id=%s",
                       (data.usuario, data.contrasena, usuario_id))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/usuarios/{usuario_id}")
def eliminar_usuario(usuario_id: int, admin=Depends(verificar_admin)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM usuarios WHERE id=%s", (usuario_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ENDPOINTS CONSULTA
# ─────────────────────────────────────────────
@app.get("/pacientes/{paciente_id}/sesion")
def obtener_sesion(paciente_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id FROM sesiones WHERE paciente_id = %s
        ORDER BY fecha DESC LIMIT 1
    """, (paciente_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row or {"id": None}

@app.get("/sesiones/{sesion_id}/horas")
def obtener_horas(sesion_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT hs.id, hs.numero_hora, hs.hora_inicio, hs.hora_fin,
               COUNT(i.id) AS total_interrupciones
        FROM horas_sesion hs
        LEFT JOIN interrupciones i ON i.hora_sesion_id = hs.id
        WHERE hs.sesion_id = %s
        GROUP BY hs.id ORDER BY hs.numero_hora
    """, (sesion_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.get("/horas/{hora_sesion_id}/interrupciones")
def obtener_interrupciones(hora_sesion_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM interrupciones
        WHERE hora_sesion_id = %s ORDER BY numero_interrupcion
    """, (hora_sesion_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.get("/interrupciones/{interrupcion_id}/senales/{tipo}")
def obtener_senales(interrupcion_id: int, tipo: str):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT timestamp_ms, valor FROM senales_esp32
        WHERE interrupcion_id = %s AND tipo_senal = %s
        ORDER BY timestamp_ms
    """, (interrupcion_id, tipo))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

# ─────────────────────────────────────────────
# PANEL DE ADMINISTRACIÓN
# ─────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(admin=Depends(verificar_admin)):
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AOS — Panel Admin</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: Arial, sans-serif; background: #FFFFFF; color: #2C4A5A; }
            .banner { background: #EEF5FB; padding: 14px 30px;
                      border-bottom: 1px solid #D4E8F3; display: flex;
                      align-items: center; justify-content: space-between; }
            .banner h1 { font-family: 'Times New Roman', serif;
                         font-size: 22px; color: #2C4A5A; }
            .tabs { display: flex; background: #EEF5FB;
                    border-bottom: 2px solid #D4E8F3; padding: 0 30px; }
            .tab { padding: 12px 24px; cursor: pointer; font-weight: bold;
                   font-size: 13px; color: #5A7A8A; border-bottom: 3px solid transparent; }
            .tab.active { color: #7AAFC5; border-bottom: 3px solid #7AAFC5; }
            .content { padding: 24px 30px; }
            .section { display: none; }
            .section.active { display: block; }
            .toolbar { display: flex; justify-content: space-between;
                       align-items: center; margin-bottom: 16px; }
            .search { background: #EEF5FB; border: 1px solid #D4E8F3;
                      padding: 8px 14px; width: 300px; border-radius: 4px;
                      font-size: 13px; color: #2C4A5A; }
            .btn { padding: 8px 18px; border: none; border-radius: 4px;
                   cursor: pointer; font-size: 13px; font-weight: bold; }
            .btn-primary { background: #7AAFC5; color: white; }
            .btn-primary:hover { background: #5B9AB5; }
            .btn-danger  { background: #D65C5C; color: white; font-size: 11px; padding: 5px 10px; }
            .btn-edit    { background: #EEF5FB; color: #2C4A5A; font-size: 11px; padding: 5px 10px; border: 1px solid #D4E8F3; }
            table { width: 100%; border-collapse: collapse; }
            th { background: #EEF5FB; color: #2C4A5A; padding: 10px;
                 text-align: left; font-size: 13px; border-bottom: 2px solid #D4E8F3; }
            td { padding: 10px; border-bottom: 1px solid #D4E8F3; font-size: 13px; }
            tr:hover { background: #F5FAFD; }
            .modal-bg { display: none; position: fixed; top: 0; left: 0;
                        width: 100%; height: 100%; background: rgba(0,0,0,0.3);
                        z-index: 100; justify-content: center; align-items: center; }
            .modal-bg.show { display: flex; }
            .modal { background: white; border-radius: 8px; padding: 28px;
                     width: 460px; box-shadow: 0 8px 32px rgba(44,74,90,0.15); }
            .modal h2 { font-family: 'Times New Roman', serif; color: #2C4A5A;
                        margin-bottom: 20px; font-size: 18px; }
            .form-group { margin-bottom: 14px; }
            .form-group label { display: block; font-size: 12px; color: #5A7A8A;
                                margin-bottom: 4px; font-weight: bold; }
            .form-group input, .form-group select {
                width: 100%; padding: 8px 12px; border: 1px solid #D4E8F3;
                border-radius: 4px; font-size: 13px; background: #EEF5FB;
                color: #2C4A5A; }
            .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
            .modal-btns { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
            .btn-cancel { background: #EEF5FB; color: #5A7A8A;
                          border: 1px solid #D4E8F3; }
            .badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }
            .badge-ok   { background: #EEF8F2; color: #2E7D52; }
            .badge-warn { background: #FFF8EC; color: #B07020; }
            .badge-crit { background: #FFF0EE; color: #A02020; }
        </style>
    </head>
    <body>
        <div class="banner">
            <h1>⚙️ AOS — Panel de Administración</h1>
            <a href="/dashboard" style="color:#7AAFC5; font-size:13px;">Ver Dashboard →</a>
        </div>

        <div class="tabs">
            <div class="tab active" onclick="cambiarTab('pacientes')">👥 Pacientes</div>
            <div class="tab" onclick="cambiarTab('usuarios')">🔑 Usuarios</div>
        </div>

        <!-- PACIENTES -->
        <div class="content">
            <div id="sec-pacientes" class="section active">
                <div class="toolbar">
                    <input class="search" id="buscar-pac" placeholder="🔍 Buscar paciente..."
                           oninput="filtrarPacientes()">
                    <button class="btn btn-primary" onclick="abrirModalPaciente()">+ Nuevo paciente</button>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Nombre</th><th>Fecha estudio</th><th>Edad</th>
                            <th>Sexo</th><th>Enf. cardiovascular</th>
                            <th>IMC</th><th>EPWORTH</th><th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody id="tbody-pacientes"></tbody>
                </table>
            </div>

            <!-- USUARIOS -->
            <div id="sec-usuarios" class="section">
                <div class="toolbar">
                    <span style="font-size:13px; color:#5A7A8A;">Gestión de usuarios del sistema</span>
                    <button class="btn btn-primary" onclick="abrirModalUsuario()">+ Nuevo usuario</button>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th><th>Usuario</th><th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody id="tbody-usuarios"></tbody>
                </table>
            </div>
        </div>

        <!-- MODAL PACIENTE -->
        <div class="modal-bg" id="modal-paciente">
            <div class="modal">
                <h2 id="modal-pac-titulo">Nuevo paciente</h2>
                <input type="hidden" id="pac-id">
                <div class="form-group">
                    <label>Nombre completo</label>
                    <input id="pac-nombre" placeholder="Ej: Juan Pérez García">
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Fecha de estudio</label>
                        <input id="pac-fecha" type="date">
                    </div>
                    <div class="form-group">
                        <label>Edad</label>
                        <input id="pac-edad" type="number" placeholder="Ej: 45">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Sexo</label>
                        <select id="pac-sexo">
                            <option value="">Seleccionar</option>
                            <option value="M">Masculino</option>
                            <option value="F">Femenino</option>
                            <option value="Otro">Otro</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Enf. cardiovascular</label>
                        <select id="pac-cardio">
                            <option value="">Seleccionar</option>
                            <option value="Si">Si</option>
                            <option value="No">No</option>
                        </select>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>IMC</label>
                        <input id="pac-imc" type="number" step="0.1" placeholder="Ej: 27.5">
                    </div>
                    <div class="form-group">
                        <label>EPWORTH (0-24)</label>
                        <input id="pac-epworth" type="number" min="0" max="24" placeholder="Ej: 12">
                    </div>
                </div>
                <div class="modal-btns">
                    <button class="btn btn-cancel" onclick="cerrarModals()">Cancelar</button>
                    <button class="btn btn-primary" onclick="guardarPaciente()">Guardar</button>
                </div>
            </div>
        </div>

        <!-- MODAL USUARIO -->
        <div class="modal-bg" id="modal-usuario">
            <div class="modal">
                <h2 id="modal-usr-titulo">Nuevo usuario</h2>
                <input type="hidden" id="usr-id">
                <div class="form-group">
                    <label>Nombre de usuario</label>
                    <input id="usr-nombre" placeholder="Ej: doctor_lopez">
                </div>
                <div class="form-group">
                    <label>Contraseña</label>
                    <input id="usr-pass" type="password" placeholder="Contraseña">
                </div>
                <div class="modal-btns">
                    <button class="btn btn-cancel" onclick="cerrarModals()">Cancelar</button>
                    <button class="btn btn-primary" onclick="guardarUsuario()">Guardar</button>
                </div>
            </div>
        </div>

        <script>
            let pacientes = [];

            function cambiarTab(tab) {
                document.querySelectorAll('.tab').forEach((t,i) => {
                    t.classList.toggle('active', (i===0&&tab==='pacientes')||(i===1&&tab==='usuarios'));
                });
                document.getElementById('sec-pacientes').classList.toggle('active', tab==='pacientes');
                document.getElementById('sec-usuarios').classList.toggle('active', tab==='usuarios');
                if (tab === 'usuarios') cargarUsuarios();
            }

            // ── PACIENTES ──
            async function cargarPacientes() {
                const res = await fetch('/pacientes');
                pacientes = await res.json();
                mostrarPacientes(pacientes);
            }

            function mostrarPacientes(datos) {
                const tb = document.getElementById('tbody-pacientes');
                tb.innerHTML = '';
                datos.forEach(p => {
                    const epw = p.epworth >= 15 ? 'badge-crit' : p.epworth >= 10 ? 'badge-warn' : 'badge-ok';
                    const imc = p.imc >= 30 ? 'badge-crit' : p.imc >= 25 ? 'badge-warn' : 'badge-ok';
                    tb.innerHTML += '<tr>' +
                        '<td><strong>' + p.nombre + '</strong></td>' +
                        '<td>' + (p.fecha_estudio||'--') + '</td>' +
                        '<td>' + (p.edad||'--') + '</td>' +
                        '<td>' + (p.sexo||'--') + '</td>' +
                        '<td>' + (p.enfermedad_cardiovascular||'--') + '</td>' +
                        '<td><span class="badge ' + imc + '">' + (p.imc||'--') + '</span></td>' +
                        '<td><span class="badge ' + epw + '">' + (p.epworth||'--') + '</span></td>' +
                        '<td>' +
                            '<button class="btn btn-edit" onclick="editarPaciente(' + JSON.stringify(p).replace(/"/g,"'") + ')">✏️ Editar</button> ' +
                            '<button class="btn btn-danger" onclick="eliminarPaciente(' + p.id + ')">🗑️ Eliminar</button>' +
                        '</td></tr>';
                });
            }

            function filtrarPacientes() {
                const q = document.getElementById('buscar-pac').value.toLowerCase();
                mostrarPacientes(pacientes.filter(p => p.nombre.toLowerCase().includes(q)));
            }

            function abrirModalPaciente() {
                document.getElementById('modal-pac-titulo').innerText = 'Nuevo paciente';
                document.getElementById('pac-id').value = '';
                ['pac-nombre','pac-fecha','pac-edad','pac-imc','pac-epworth'].forEach(id => document.getElementById(id).value = '');
                document.getElementById('pac-sexo').value = '';
                document.getElementById('pac-cardio').value = '';
                document.getElementById('modal-paciente').classList.add('show');
            }

            function editarPaciente(p) {
                document.getElementById('modal-pac-titulo').innerText = 'Editar paciente';
                document.getElementById('pac-id').value = p.id;
                document.getElementById('pac-nombre').value = p.nombre || '';
                document.getElementById('pac-fecha').value = p.fecha_estudio || '';
                document.getElementById('pac-edad').value = p.edad || '';
                document.getElementById('pac-sexo').value = p.sexo || '';
                document.getElementById('pac-cardio').value = p.enfermedad_cardiovascular || '';
                document.getElementById('pac-imc').value = p.imc || '';
                document.getElementById('pac-epworth').value = p.epworth || '';
                document.getElementById('modal-paciente').classList.add('show');
            }

            async function guardarPaciente() {
                const id = document.getElementById('pac-id').value;
                const body = {
                    nombre: document.getElementById('pac-nombre').value,
                    fecha_estudio: document.getElementById('pac-fecha').value || null,
                    edad: parseInt(document.getElementById('pac-edad').value) || null,
                    sexo: document.getElementById('pac-sexo').value || null,
                    enfermedad_cardiovascular: document.getElementById('pac-cardio').value || null,
                    imc: parseFloat(document.getElementById('pac-imc').value) || null,
                    epworth: parseInt(document.getElementById('pac-epworth').value) || null
                };
                const url = id ? '/pacientes/' + id : '/pacientes';
                const method = id ? 'PUT' : 'POST';
                await fetch(url, {
                    method,
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                cerrarModals();
                cargarPacientes();
            }

            async function eliminarPaciente(id) {
                if (!confirm('¿Eliminar este paciente?')) return;
                await fetch('/pacientes/' + id, { method: 'DELETE' });
                cargarPacientes();
            }

            // ── USUARIOS ──
            async function cargarUsuarios() {
                const res = await fetch('/usuarios');
                const usuarios = await res.json();
                const tb = document.getElementById('tbody-usuarios');
                tb.innerHTML = '';
                usuarios.forEach(u => {
                    tb.innerHTML += '<tr>' +
                        '<td>' + u.id + '</td>' +
                        '<td><strong>' + u.usuario + '</strong></td>' +
                        '<td>' +
                            '<button class="btn btn-edit" onclick="editarUsuario(' + u.id + ',\'' + u.usuario + '\')">✏️ Editar</button> ' +
                            '<button class="btn btn-danger" onclick="eliminarUsuario(' + u.id + ')">🗑️ Eliminar</button>' +
                        '</td></tr>';
                });
            }

            function abrirModalUsuario() {
                document.getElementById('modal-usr-titulo').innerText = 'Nuevo usuario';
                document.getElementById('usr-id').value = '';
                document.getElementById('usr-nombre').value = '';
                document.getElementById('usr-pass').value = '';
                document.getElementById('modal-usuario').classList.add('show');
            }

            function editarUsuario(id, nombre) {
                document.getElementById('modal-usr-titulo').innerText = 'Editar usuario';
                document.getElementById('usr-id').value = id;
                document.getElementById('usr-nombre').value = nombre;
                document.getElementById('usr-pass').value = '';
                document.getElementById('modal-usuario').classList.add('show');
            }

            async function guardarUsuario() {
                const id = document.getElementById('usr-id').value;
                const body = {
                    usuario: document.getElementById('usr-nombre').value,
                    contrasena: document.getElementById('usr-pass').value
                };
                const url = id ? '/usuarios/' + id : '/usuarios';
                const method = id ? 'PUT' : 'POST';
                await fetch(url, {
                    method,
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                cerrarModals();
                cargarUsuarios();
            }

            async function eliminarUsuario(id) {
                if (!confirm('¿Eliminar este usuario?')) return;
                await fetch('/usuarios/' + id, { method: 'DELETE' });
                cargarUsuarios();
            }

            function cerrarModals() {
                document.getElementById('modal-paciente').classList.remove('show');
                document.getElementById('modal-usuario').classList.remove('show');
            }

            cargarPacientes();
        </script>
    </body>
    </html>
    """

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AOS - Monitor</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: Arial, sans-serif; background: #FFFFFF; color: #2C4A5A; }
            .banner { background: #EEF5FB; padding: 14px 30px;
                      border-bottom: 1px solid #D4E8F3; display: flex;
                      align-items: center; justify-content: space-between; }
            .banner h1 { font-family: 'Times New Roman', serif; font-size: 22px; color: #2C4A5A; }
            .content { padding: 20px 30px; }
            .search-bar { background: #EEF5FB; border: 1px solid #D4E8F3;
                          padding: 8px 14px; width: 400px; border-radius: 4px;
                          font-size: 14px; color: #2C4A5A; }
            table { width: 100%; border-collapse: collapse; margin-top: 16px; }
            th { background: #EEF5FB; color: #2C4A5A; padding: 10px;
                 text-align: left; font-size: 13px; border-bottom: 2px solid #D4E8F3; }
            td { padding: 10px; border-bottom: 1px solid #D4E8F3; font-size: 13px; }
            tr:hover { background: #F5FAFD; }
            .badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }
            .badge-ok   { background: #EEF8F2; color: #2E7D52; }
            .badge-warn { background: #FFF8EC; color: #B07020; }
            .badge-crit { background: #FFF0EE; color: #A02020; }
            .section-title { font-size: 14px; font-weight: bold; color: #5A7A8A;
                             margin: 20px 0 10px; text-transform: uppercase; letter-spacing: 1px; }
        </style>
    </head>
    <body>
        <div class="banner">
            <h1>📋 AOS — Base de Datos de Pacientes</h1>
            <a href="/admin" style="color:#7AAFC5; font-size:13px;">Panel Admin →</a>
        </div>
        <div class="content">
            <input class="search-bar" id="buscador" placeholder="🔍 Buscar paciente..."
                   oninput="filtrar()">
            <div class="section-title">Pacientes registrados</div>
            <table>
                <thead>
                    <tr>
                        <th>Nombre</th><th>Fecha estudio</th><th>Edad</th>
                        <th>Sexo</th><th>Enf. cardiovascular</th>
                        <th>IMC</th><th>EPWORTH</th>
                    </tr>
                </thead>
                <tbody id="cuerpo"></tbody>
            </table>
        </div>
        <script>
            let pacientes = [];
            async function cargar() {
                const res = await fetch('/pacientes');
                pacientes = await res.json();
                mostrar(pacientes);
            }
            function mostrar(datos) {
                const cuerpo = document.getElementById('cuerpo');
                cuerpo.innerHTML = '';
                datos.forEach(p => {
                    const epw = p.epworth >= 15 ? 'badge-crit' : p.epworth >= 10 ? 'badge-warn' : 'badge-ok';
                    const imc = p.imc >= 30 ? 'badge-crit' : p.imc >= 25 ? 'badge-warn' : 'badge-ok';
                    cuerpo.innerHTML += '<tr>' +
                        '<td><strong>' + p.nombre + '</strong></td>' +
                        '<td>' + (p.fecha_estudio||'--') + '</td>' +
                        '<td>' + (p.edad||'--') + '</td>' +
                        '<td>' + (p.sexo||'--') + '</td>' +
                        '<td>' + (p.enfermedad_cardiovascular||'--') + '</td>' +
                        '<td><span class="badge ' + imc + '">' + (p.imc||'--') + '</span></td>' +
                        '<td><span class="badge ' + epw + '">' + (p.epworth||'--') + '</span></td>' +
                        '</tr>';
                });
            }
            function filtrar() {
                const q = document.getElementById('buscador').value.toLowerCase();
                mostrar(pacientes.filter(p => p.nombre.toLowerCase().includes(q)));
            }
            cargar();
        </script>
    </body>
    </html>
    """

@app.get("/")
def home():
    return {"mensaje": "API AOS activa", "endpoints": [
        "/dashboard", "/admin", "/pacientes", "/senales", "/interrupciones"
    ]}
