from fastapi import FastAPI, HTTPException, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import mysql.connector
import os
import bcrypt
import json
import math as _math
from pydantic import BaseModel
from typing import List

app = FastAPI()
def crear_tabla():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS senales_esp32 (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paciente VARCHAR(50),
            tipo_senal VARCHAR(20),
            timestamp_ms BIGINT,
            valor FLOAT
        )
        """)

        conn.commit()
        cursor.close()
        conn.close()

        print("✅ Tabla creada correctamente")

    except Exception as e:
        print("❌ Error creando tabla:", e)

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

# ─────────────────────────────────────────────
# NUEVO MODELO: Muestra de streaming en tiempo real
# Enviada por el ESP32 cada INTERVALO_STREAM_MS ms (200 ms)
# Campos: paciente, timestamp_ms, ecg, spo2, acce_z, flujo
# ─────────────────────────────────────────────
class MuestraStream(BaseModel):
    paciente: str
    timestamp_ms: int
    ecg: float
    spo2: float
    acce_z: float
    flujo: float

def timedelta_a_str(valor):
    if valor is None:
        return None
    if hasattr(valor, 'total_seconds'):
        total = int(valor.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return str(valor)

# ─────────────────────────────────────────────
# WEBSOCKETS (TRANSMISIÓN EN TIEMPO REAL)
# ─────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_browsers: List[WebSocket] = []

    async def connect_browser(self, websocket: WebSocket):
        await websocket.accept()
        self.active_browsers.append(websocket)

    def disconnect_browser(self, websocket: WebSocket):
        if websocket in self.active_browsers:
            self.active_browsers.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_browsers[:]:
            try:
                await connection.send_text(message)
            except Exception:
                self.active_browsers.remove(connection)

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_esp32(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await manager.broadcast(data)
    except WebSocketDisconnect:
        print("ESP32 desconectado del WebSocket")

@app.websocket("/ws/browser")
async def websocket_browser(websocket: WebSocket):
    await manager.connect_browser(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_browser(websocket)

# ─────────────────────────────────────────────
# ENDPOINT /stream — RECIBE MUESTRAS EN TIEMPO REAL DEL ESP32
# El ESP32 hace POST aquí cada 200 ms con una muestra de todas las señales.
# La API reenvía el dato via WebSocket a los navegadores conectados.
# ─────────────────────────────────────────────
import asyncio

@app.post("/stream")
async def recibir_stream(muestra: MuestraStream):
    """
    Recibe una muestra en tiempo real del ESP32 y la difunde
    a todos los navegadores suscritos via WebSocket (/ws/browser).
    Formato enviado al navegador (JSON):
      {
        "paciente":     "Prosim",
        "timestamp_ms": 12345,
        "ecg":          42.5,
        "spo2":         97,
        "acce_z":       0.123,
        "flujo":        1800,
        "tipo":         "stream"
      }
    """
    payload = {
        "tipo":         "stream",
        "paciente":     muestra.paciente,
        "timestamp_ms": muestra.timestamp_ms,
        "ecg":          muestra.ecg,
        "spo2":         muestra.spo2,
        "acce_z":       muestra.acce_z,
        "flujo":        muestra.flujo,
    }
    await manager.broadcast(json.dumps(payload))
    return {"status": "ok"}

# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
def login_page(error: Optional[str] = None):
    error_html = '<p style="color:#D65C5C; text-align:center; font-size:13px; margin-bottom:12px;">Usuario o contraseña incorrectos</p>' if error else ''
    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AOS — Login</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: Arial, sans-serif; background: #EEF5FB; display: flex; justify-content: center; align-items: center; height: 100vh; }}
            .card {{ background: white; border-radius: 10px; padding: 40px; width: 380px; box-shadow: 0 8px 32px rgba(44,74,90,0.12); border: 1px solid #D4E8F3; }}
            h1 {{ font-family: 'Times New Roman', serif; color: #2C4A5A; text-align: center; margin-bottom: 8px; font-size: 24px; }}
            p {{ text-align: center; color: #5A7A8A; font-size: 13px; margin-bottom: 28px; }}
            label {{ display: block; font-size: 12px; color: #5A7A8A; font-weight: bold; margin-bottom: 4px; }}
            input {{ width: 100%; padding: 10px 14px; border: 1px solid #D4E8F3; border-radius: 4px; font-size: 14px; background: #EEF5FB; color: #2C4A5A; margin-bottom: 16px; }}
            button {{ width: 100%; padding: 11px; background: #7AAFC5; color: white; border: none; border-radius: 4px; font-size: 15px; font-weight: bold; cursor: pointer; }}
            button:hover {{ background: #5B9AB5; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>⚙️ AOS Admin</h1>
            <p>Panel de Administración</p>
            {error_html}
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
    except Exception as e:
        pass
    return RedirectResponse(url="/login?error=1", status_code=302)

@app.post("/api/login")
def api_login_json(data: LoginRequest):
    if data.usuario == ADMIN_USER and data.contrasena == ADMIN_PASS:
        return {"status": "ok", "usuario": {"id": 0, "usuario": ADMIN_USER}}
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, usuario, contrasena FROM usuarios WHERE usuario = %s", (data.usuario,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if user and check_password(data.contrasena, user["contrasena"]):
            return {"status": "ok", "usuario": {"id": user["id"], "usuario": user["usuario"]}}
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
# ENDPOINTS PACIENTES
# ─────────────────────────────────────────────
@app.get("/pacientes")
@app.get("/api/pacientes")
def get_pacientes():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM pacientes ORDER BY fecha_estudio DESC")
        rows = cursor.fetchall()
        for r in rows:
            if r.get("fecha_estudio"):
                r["fecha_estudio"] = str(r["fecha_estudio"])
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pacientes")
def create_paciente(pac: PacienteModel, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pacientes (nombre, fecha_estudio, edad, sexo, enfermedad_cardiovascular, imc, epworth)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (pac.nombre, pac.fecha_estudio, pac.edad, pac.sexo, pac.enfermedad_cardiovascular, pac.imc, pac.epworth))
        conn.commit()
        pac_id = cursor.lastrowid
        cursor.close()
        conn.close()
        return {"id": pac_id, "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/pacientes/{paciente_id}")
def update_paciente(paciente_id: int, pac: PacienteModel, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pacientes
            SET nombre=%s, fecha_estudio=%s, edad=%s, sexo=%s, enfermedad_cardiovascular=%s, imc=%s, epworth=%s
            WHERE id=%s
        """, (pac.nombre, pac.fecha_estudio, pac.edad, pac.sexo, pac.enfermedad_cardiovascular, pac.imc, pac.epworth, paciente_id))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/pacientes/{paciente_id}")
def delete_paciente(paciente_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sesiones WHERE paciente_id = %s", (paciente_id,))
        sesiones = [r[0] for r in cursor.fetchall()]
        for sesion_id in sesiones:
            cursor.execute("SELECT id FROM horas_sesion WHERE sesion_id = %s", (sesion_id,))
            horas = [r[0] for r in cursor.fetchall()]
            for hora_id in horas:
                cursor.execute("SELECT id FROM interrupciones WHERE hora_sesion_id = %s", (hora_id,))
                interrupciones = [r[0] for r in cursor.fetchall()]
                for interr_id in interrupciones:
                    cursor.execute("DELETE FROM senales_esp32 WHERE interrupcion_id = %s", (interr_id,))
                cursor.execute("DELETE FROM interrupciones WHERE hora_sesion_id = %s", (hora_id,))
            cursor.execute("DELETE FROM horas_sesion WHERE sesion_id = %s", (sesion_id,))
        cursor.execute("DELETE FROM sesiones WHERE paciente_id = %s", (paciente_id,))
        cursor.execute("DELETE FROM pacientes WHERE id=%s", (paciente_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "eliminado": "paciente", "id": paciente_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ENDPOINTS USUARIOS
# ─────────────────────────────────────────────
@app.get("/usuarios")
@app.get("/api/usuarios")
def get_usuarios(request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, usuario FROM usuarios ORDER BY id")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/usuarios")
def create_usuario(usr: UsuarioModel, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        hashed = hash_password(usr.contrasena)
        cursor.execute("INSERT INTO usuarios (usuario, contrasena) VALUES (%s, %s)", (usr.usuario, hashed))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/usuarios/{usuario_id}")
def delete_usuario(usuario_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
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
# ENDPOINTS SESIONES
# ─────────────────────────────────────────────
@app.get("/sesion/por-paciente/{paciente_id}")
def sesion_por_paciente_id(paciente_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM sesiones WHERE paciente_id = %s ORDER BY fecha DESC LIMIT 1", (paciente_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return {"sesion_id": row["id"] if row else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sesion/por-nombre/{nombre}")
def sesion_por_nombre(nombre: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT s.id FROM sesiones s JOIN pacientes p ON s.paciente_id = p.id
            WHERE p.nombre = %s ORDER BY s.fecha DESC LIMIT 1
        """, (nombre,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return {"sesion_id": row["id"] if row else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sesiones/por-paciente/{paciente_id}")
def sesiones_por_paciente(paciente_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, fecha FROM sesiones WHERE paciente_id = %s ORDER BY fecha DESC", (paciente_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        result = []
        for r in rows:
            r = dict(r)
            r["fecha"] = str(r["fecha"]) if r.get("fecha") else None
            result.append(r)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/sesiones/{sesion_id}")
def eliminar_sesion(sesion_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM horas_sesion WHERE sesion_id = %s", (sesion_id,))
        horas = [r[0] for r in cursor.fetchall()]
        for hora_id in horas:
            cursor.execute("SELECT id FROM interrupciones WHERE hora_sesion_id = %s", (hora_id,))
            interrupciones = [r[0] for r in cursor.fetchall()]
            for interr_id in interrupciones:
                cursor.execute("DELETE FROM senales_esp32 WHERE interrupcion_id = %s", (interr_id,))
            cursor.execute("DELETE FROM interrupciones WHERE hora_sesion_id = %s", (hora_id,))
        cursor.execute("DELETE FROM horas_sesion WHERE sesion_id = %s", (sesion_id,))
        cursor.execute("DELETE FROM sesiones WHERE id = %s", (sesion_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "eliminado": "sesion", "id": sesion_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ENDPOINTS HORAS DE SESIÓN
# ─────────────────────────────────────────────
@app.get("/horas-sesion/{sesion_id}")
def horas_sesion_endpoint(sesion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT hs.id, hs.numero_hora, hs.hora_inicio, hs.hora_fin, COUNT(i.id) AS total_interrupciones
            FROM horas_sesion hs
            LEFT JOIN interrupciones i ON i.hora_sesion_id = hs.id
            WHERE hs.sesion_id = %s
            GROUP BY hs.id ORDER BY hs.numero_hora
        """, (sesion_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        result = []
        for idx, r in enumerate(rows):
            r = dict(r)
            r["hora_inicio"] = timedelta_a_str(r.get("hora_inicio"))
            r["hora_fin"]    = timedelta_a_str(r.get("hora_fin"))
            r["hora_orden"]  = idx + 1
            r["hora_real"]   = r["numero_hora"]
            result.append(r)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/horas-sesion/{hora_sesion_id}")
def eliminar_hora_sesion(hora_sesion_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM interrupciones WHERE hora_sesion_id = %s", (hora_sesion_id,))
        interrupciones = [r[0] for r in cursor.fetchall()]
        for interr_id in interrupciones:
            cursor.execute("DELETE FROM senales_esp32 WHERE interrupcion_id = %s", (interr_id,))
        cursor.execute("DELETE FROM interrupciones WHERE hora_sesion_id = %s", (hora_sesion_id,))
        cursor.execute("DELETE FROM horas_sesion WHERE id = %s", (hora_sesion_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "eliminado": "hora_sesion", "id": hora_sesion_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ENDPOINTS INTERRUPCIONES
# ─────────────────────────────────────────────
@app.get("/interrupciones/{hora_sesion_id}")
def interrupciones_por_hora(hora_sesion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT sesion_id FROM horas_sesion WHERE id = %s", (hora_sesion_id,))
        sesion_row = cursor.fetchone()
        cursor.execute("""
            SELECT id, numero_interrupcion, hora_detectada, duracion_segundos, spo2, frecuencia_cardiaca, anotacion
            FROM interrupciones WHERE hora_sesion_id = %s ORDER BY id
        """, (hora_sesion_id,))
        rows = cursor.fetchall()
        offset = 0
        if sesion_row:
            sesion_id = sesion_row["sesion_id"]
            cursor.execute("""
                SELECT COUNT(i.id) as cnt FROM interrupciones i
                JOIN horas_sesion hs ON i.hora_sesion_id = hs.id
                WHERE hs.sesion_id = %s AND hs.id < %s
            """, (sesion_id, hora_sesion_id))
            offset_row = cursor.fetchone()
            if offset_row:
                offset = offset_row["cnt"]
        cursor.close()
        conn.close()
        result = []
        for local_idx, r in enumerate(rows):
            r = dict(r)
            r["hora_detectada"] = timedelta_a_str(r.get("hora_detectada"))
            r["numero_consecutivo"] = offset + local_idx + 1
            result.append(r)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/interrupciones-sesion/{sesion_id}")
def interrupciones_por_sesion(sesion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT i.id, i.numero_interrupcion, i.hora_detectada, i.duracion_segundos, i.spo2, i.frecuencia_cardiaca,
                   i.anotacion, hs.numero_hora,
                   (SELECT COUNT(*) FROM senales_esp32 WHERE interrupcion_id = i.id) AS total_senales
            FROM interrupciones i JOIN horas_sesion hs ON i.hora_sesion_id = hs.id
            WHERE hs.sesion_id = %s ORDER BY i.id
        """, (sesion_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        result = []
        for global_idx, r in enumerate(rows):
            r = dict(r)
            r["hora_detectada"] = timedelta_a_str(r.get("hora_detectada"))
            r["numero_consecutivo"] = global_idx + 1
            result.append(r)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/interrupciones/{interrupcion_id}/anotacion")
async def guardar_anotacion_endpoint(interrupcion_id: int, body: AnotacionModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE interrupciones SET anotacion=%s WHERE id=%s", (body.anotacion, interrupcion_id))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/interrupciones/{interrupcion_id}")
def eliminar_interrupcion(interrupcion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM senales_esp32 WHERE interrupcion_id = %s", (interrupcion_id,))
        cursor.execute("DELETE FROM interrupciones WHERE id = %s", (interrupcion_id,))
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
            INSERT INTO interrupciones
                (hora_sesion_id, numero_interrupcion, hora_detectada, duracion_segundos, spo2, frecuencia_cardiaca)
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
# ENDPOINTS SEÑALES
# ─────────────────────────────────────────────
@app.get("/senales/{interrupcion_id}/{tipo}")
def senales_por_interrupcion(interrupcion_id: int, tipo: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT timestamp_ms, valor FROM senales_esp32
            WHERE interrupcion_id = %s AND tipo_senal = %s ORDER BY timestamp_ms
        """, (interrupcion_id, tipo))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/senales-completas/{interrupcion_id}")
def senales_completas(interrupcion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT tipo_senal, timestamp_ms, valor FROM senales_esp32
            WHERE interrupcion_id = %s ORDER BY tipo_senal, timestamp_ms
        """, (interrupcion_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        raw = {}
        for r in rows:
            tipo = r["tipo_senal"]
            if tipo not in raw:
                raw[tipo] = {"timestamps": [], "valores": []}
            raw[tipo]["timestamps"].append(r["timestamp_ms"])
            raw[tipo]["valores"].append(float(r["valor"]))
        resultado = {}
        ts_ecg_ref = None
        for tipo, data in raw.items():
            ts = data["timestamps"]
            vs = data["valores"]
            if tipo.lower() == "ecg":
                ts, vs = _limpiar_outliers_ecg(ts, vs)
                ts_ecg_ref = ts
            resultado[tipo] = {"timestamps": ts, "valores": vs}
        ts_flujo = raw.get("flujo",  {}).get("timestamps", [])
        vs_flujo = raw.get("flujo",  {}).get("valores",    [])
        ts_accz  = raw.get("acce_z", {}).get("timestamps", [])
        vs_accz  = raw.get("acce_z", {}).get("valores",    [])
        ts_resp, vs_resp = _construir_resp_desde_streaming(ts_flujo, vs_flujo, ts_accz, vs_accz, ts_ecg_ref)
        resultado["frecuencia_respiratoria"] = {"timestamps": ts_resp, "valores": vs_resp}
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MuestraESP32(BaseModel):
    paciente: str
    timestamp_ms: int
    ecg: float
    spo2: float
    acce_z: float
    flujo: float

@app.post("/senales")
async def subir_senales(senales: List[MuestraESP32]):
    if not senales:
        raise HTTPException(status_code=400, detail="Lista vacía")
    try:
        print(senales)
        conn = get_db_connection()
        cursor = conn.cursor()

        sql = "INSERT INTO senales_esp32 (paciente, tipo_senal, timestamp_ms, valor) VALUES (%s, %s, %s, %s)"

        valores = []

        for s in senales:
            valores.extend([
                (s.paciente, "ecg", s.timestamp_ms, s.ecg),
                (s.paciente, "spo2", s.timestamp_ms, s.spo2),
                (s.paciente, "acce_z", s.timestamp_ms, s.acce_z),
                (s.paciente, "flujo", s.timestamp_ms, s.flujo),
            ])

        cursor.executemany(sql, valores)
        conn.commit()

        cursor.close()
        conn.close()

        return {"status": "success", "rows_inserted": len(valores)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _limpiar_outliers_ecg(timestamps, valores):
    if len(valores) < 10:
        return timestamps, valores
    sorted_v = sorted(valores)
    n = len(sorted_v)
    iqr = sorted_v[(3 * n) // 4] - sorted_v[n // 4]
    if iqr == 0:
        return timestamps, valores
    mediana = sorted_v[n // 2]
    lim_inf = mediana - 8 * iqr
    lim_sup = mediana + 8 * iqr
    return timestamps, [max(lim_inf, min(lim_sup, v)) for v in valores]

def _construir_resp_desde_streaming(ts_flujo, vs_flujo, ts_accz, vs_accz, ts_ecg_ref=None):
    def interp_en_ts(ts_src, vs_src, ts_dest):
        resultado = []
        j = 0
        for t in ts_dest:
            if t <= ts_src[0]: resultado.append(vs_src[0]); continue
            if t >= ts_src[-1]: resultado.append(vs_src[-1]); continue
            while j < len(ts_src) - 2 and ts_src[j + 1] < t: j += 1
            t_a, t_b = ts_src[j], ts_src[min(j + 1, len(ts_src) - 1)]
            v_a, v_b = vs_src[j], vs_src[min(j + 1, len(vs_src) - 1)]
            frac = (t - t_a) / (t_b - t_a) if t_b != t_a else 0.0
            resultado.append(v_a + frac * (v_b - v_a))
        return resultado

    def normalizar_al_rango(valores, ref_min, ref_max):
        if not valores: return valores
        v_min, v_max = min(valores), max(valores)
        if v_max == v_min: return [(ref_min + ref_max) / 2] * len(valores)
        escala = (ref_max - ref_min) / (v_max - v_min)
        return [ref_min + (v - v_min) * escala for v in valores]

    tiene_flujo = len(ts_flujo) > 1
    tiene_accz  = len(ts_accz) > 1
    duracion_total_ms = 20000
    if ts_ecg_ref and len(ts_ecg_ref) >= 2:
        duracion_total_ms = ts_ecg_ref[-1] - ts_ecg_ref[0]
    elif tiene_flujo:
        duracion_total_ms = ts_flujo[-1] - ts_flujo[0]
    elif tiene_accz:
        duracion_total_ms = ts_accz[-1] - ts_accz[0]
    if duracion_total_ms <= 0: duracion_total_ms = 20000
    if duracion_total_ms > 30000: duracion_total_ms = 30000

    if tiene_flujo and not tiene_accz:
        ts_uniforme = [int(ts_flujo[0] + i * duracion_total_ms / 499) for i in range(500)]
        vs_interp = interp_en_ts(ts_flujo, vs_flujo, ts_uniforme)
        ts_segundos = [i * duracion_total_ms / 1000.0 / 499 for i in range(500)]
        return ts_segundos, [round(v, 3) for v in vs_interp]
    if tiene_accz and not tiene_flujo:
        ts_uniforme = [int(ts_accz[0] + i * duracion_total_ms / 499) for i in range(500)]
        vs_interp = interp_en_ts(ts_accz, vs_accz, ts_uniforme)
        ts_segundos = [i * duracion_total_ms / 1000.0 / 499 for i in range(500)]
        return ts_segundos, [round(v, 3) for v in vs_interp]
    if tiene_flujo and tiene_accz:
        t_min = min(ts_flujo[0], ts_accz[0])
        ts_uniforme = [int(t_min + i * duracion_total_ms / 499) for i in range(500)]
        vs_flujo_i = interp_en_ts(ts_flujo, vs_flujo, ts_uniforme)
        vs_accz_i  = interp_en_ts(ts_accz,  vs_accz,  ts_uniforme)
        vs_accz_norm = normalizar_al_rango(vs_accz_i, min(vs_flujo_i), max(vs_flujo_i))
        vs_comb = [0.70 * f + 0.30 * a for f, a in zip(vs_flujo_i, vs_accz_norm)]
        ts_segundos = [i * duracion_total_ms / 1000.0 / 499 for i in range(500)]
        return ts_segundos, [round(v, 3) for v in vs_comb]
    ts_segundos = [i * (duracion_total_ms / 1000.0) / 499 for i in range(500)]
    vs_sintetico = [round(155 + 30 * _math.sin(2 * _math.pi * 0.25 * t), 3) for t in ts_segundos]
    return ts_segundos, vs_sintetico

# ─────────────────────────────────────────────
# ENDPOINTS ESP32
# ─────────────────────────────────────────────
@app.get("/datos-sensores")
def obtener_datos_sensores():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.nombre AS paciente_nombre,
                i.hora_detectada, i.spo2, i.frecuencia_cardiaca AS ecg,
                i.duracion_segundos AS duracion_apnea, i.numero_interrupcion AS numero_apnea,
                (SELECT valor FROM senales_esp32 WHERE interrupcion_id = i.id AND tipo_senal = 'acce_z' LIMIT 1) AS acce_z,
                (SELECT valor FROM senales_esp32 WHERE interrupcion_id = i.id AND tipo_senal = 'flujo' LIMIT 1) AS flujo
            FROM interrupciones i
            JOIN horas_sesion hs ON i.hora_sesion_id = hs.id
            JOIN sesiones s ON hs.sesion_id = s.id
            JOIN pacientes p ON s.paciente_id = p.id
            ORDER BY i.id DESC
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        result = []
        for r in rows:
            r = dict(r)
            r["hora_detectada"] = timedelta_a_str(r.get("hora_detectada"))
            result.append(r)
        return result
    except Exception as e:
        return {"error": str(e)}

@app.post("/subir-datos")
async def subir_datos(datos: DatosESP32):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM pacientes WHERE nombre = %s LIMIT 1", (datos.paciente,))
        fila = cursor.fetchone()
        if fila:
            paciente_id = fila[0]
        else:
            from datetime import date
            cursor.execute("INSERT INTO pacientes (nombre, fecha_estudio) VALUES (%s, %s)",
                           (datos.paciente, date.today().isoformat()))
            conn.commit()
            paciente_id = cursor.lastrowid
        cursor.execute("SELECT id FROM sesiones WHERE paciente_id = %s AND DATE(fecha) = CURDATE() LIMIT 1", (paciente_id,))
        fila = cursor.fetchone()
        if fila:
            sesion_id = fila[0]
        else:
            cursor.execute("INSERT INTO sesiones (paciente_id) VALUES (%s)", (paciente_id,))
            conn.commit()
            sesion_id = cursor.lastrowid
        partes   = datos.hora.split(":")
        hora_num = int(partes[0])
        hora_ini = f"{hora_num:02d}:00:00"
        hora_fin = f"{(hora_num + 1) % 24:02d}:00:00"
        cursor.execute("SELECT id FROM horas_sesion WHERE sesion_id = %s AND numero_hora = %s LIMIT 1", (sesion_id, hora_num))
        fila = cursor.fetchone()
        if fila:
            hora_sesion_id = fila[0]
        else:
            cursor.execute("INSERT INTO horas_sesion (sesion_id, numero_hora, hora_inicio, hora_fin) VALUES (%s, %s, %s, %s)",
                           (sesion_id, hora_num, hora_ini, hora_fin))
            conn.commit()
            hora_sesion_id = cursor.lastrowid
        cursor.execute("""
            SELECT COUNT(i.id) as total FROM interrupciones i
            JOIN horas_sesion hs ON i.hora_sesion_id = hs.id WHERE hs.sesion_id = %s
        """, (sesion_id,))
        conteo = cursor.fetchone()
        numero_consecutivo = (conteo[0] if conteo else 0) + 1
        cursor.execute("""
            INSERT INTO interrupciones (hora_sesion_id, numero_interrupcion, hora_detectada, duracion_segundos, spo2, frecuencia_cardiaca)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (hora_sesion_id, numero_consecutivo, datos.hora, datos.duracion, datos.spo2, datos.ecg))
        conn.commit()
        interrupcion_id = cursor.lastrowid
        timestamp_ms = int(hora_num * 3600000)
        cursor.executemany(
            "INSERT INTO senales_esp32 (interrupcion_id, tipo_senal, timestamp_ms, valor) VALUES (%s, %s, %s, %s)",
            [(interrupcion_id, "acce_z", timestamp_ms, datos.acce_z),
             (interrupcion_id, "flujo",  timestamp_ms, datos.flujo)]
        )
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "paciente_id": paciente_id, "sesion_id": sesion_id,
                "hora_sesion_id": hora_sesion_id, "interrupcion_id": interrupcion_id,
                "numero_consecutivo": numero_consecutivo}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# PANEL DE ADMINISTRACIÓN — FRONTEND UNIFICADO
# ─────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    if not verificar_sesion(request):
        return RedirectResponse(url="/login")
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AOS — Panel Admin</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: Arial, sans-serif; background: #EEF5FB; color: #2C4A5A; }

            /* ── Header ── */
            .banner { background: white; padding: 16px 30px; border-bottom: 1px solid #D4E8F3;
                      display: flex; align-items: center; justify-content: space-between;
                      box-shadow: 0 2px 10px rgba(44,74,90,0.05); }
            .banner h1 { font-family: 'Times New Roman', serif; font-size: 22px; color: #2C4A5A; }
            .btn-logout { text-decoration: none; background: #D65C5C; color: white; padding: 8px 16px;
                          border-radius: 4px; font-size: 13px; font-weight: bold; }
            .btn-logout:hover { background: #B84B4B; }

            /* ── Nav Tabs ── */
            .tabs { display: flex; background: white; border-bottom: 2px solid #D4E8F3; padding: 0 30px; }
            .tab { padding: 14px 22px; cursor: pointer; font-weight: bold; font-size: 13px;
                   color: #5A7A8A; border-bottom: 3px solid transparent; transition: all 0.2s; }
            .tab:hover { color: #2C4A5A; }
            .tab.active { color: #7AAFC5; border-bottom: 3px solid #7AAFC5; }

            /* ── Content ── */
            .content { padding: 28px 30px; }
            .section { display: none; }
            .section.active { display: block; }

            /* ── Toolbar ── */
            .toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
            .search { background: white; border: 1px solid #D4E8F3; padding: 8px 14px; width: 300px;
                      border-radius: 4px; font-size: 13px; color: #2C4A5A; }

            /* ── Botones ── */
            .btn { padding: 8px 18px; border: none; border-radius: 4px; cursor: pointer;
                   font-size: 13px; font-weight: bold; }
            .btn-primary { background: #7AAFC5; color: white; }
            .btn-primary:hover { background: #5B9AB5; }
            .btn-danger { background: #D65C5C; color: white; font-size: 11px; padding: 5px 10px; }
            .btn-edit { background: #EEF5FB; color: #2C4A5A; font-size: 11px; padding: 5px 10px; border: 1px solid #D4E8F3; }
            .btn-del-sm { background: #D65C5C; color: white; border: none; border-radius: 4px;
                          padding: 7px 10px; cursor: pointer; font-size: 13px; flex-shrink: 0; }
            .btn-del-sm:disabled { background: #ccc; cursor: not-allowed; }

            /* ── Tablas ── */
            table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px;
                    overflow: hidden; box-shadow: 0 4px 12px rgba(44,74,90,0.04); }
            th { background: #EEF5FB; color: #2C4A5A; padding: 11px 14px; text-align: left;
                 font-size: 13px; border-bottom: 2px solid #D4E8F3; font-weight: bold;
                 text-transform: uppercase; letter-spacing: 0.4px; }
            td { padding: 11px 14px; border-bottom: 1px solid #EEF5FB; font-size: 13px; color: #2C4A5A; }

            /* ── Badges ── */
            .badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }
            .badge-ok   { background: #EEF8F2; color: #2E7D52; }
            .badge-warn { background: #FFF8EC; color: #B07020; }
            .badge-crit { background: #FFF0EE; color: #A02020; }

            /* ── Cards de pacientes ── */
            .card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }
            .card { background: white; padding: 20px; border-radius: 8px; border: 1px solid #D4E8F3;
                    box-shadow: 0 4px 12px rgba(44,74,90,0.04); }
            .card h3 { font-size: 16px; margin-bottom: 12px; color: #2C4A5A;
                       border-bottom: 1px solid #EEF5FB; padding-bottom: 8px; }
            .card p { font-size: 13px; color: #5A7A8A; margin-bottom: 6px; line-height: 1.4; }
            .card strong { color: #2C4A5A; }
            .card-actions { margin-top: 15px; display: flex; gap: 8px; }

            /* ── Señales en Vivo ── */
            .vivo-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
            .vivo-header h2 { font-size: 18px; }
            .ws-pill { display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px;
                       border-radius: 20px; font-size: 12px; font-weight: bold; background: #EEF5FB;
                       border: 1px solid #D4E8F3; color: #5A7A8A; }
            .ws-dot { width: 8px; height: 8px; border-radius: 50%; background: #ccc; }
            .ws-dot.on  { background: #4CAF50; box-shadow: 0 0 6px #4CAF50; animation: pulse 1.5s infinite; }
            .ws-dot.off { background: #D65C5C; }
            @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

            .indicadores-vivo { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
            .tarjeta-vivo { background: white; padding: 14px 18px; border-radius: 8px;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center;
                            border: 1px solid #D4E8F3; }
            .tarjeta-vivo .tv-label { font-size: 11px; color: #5A7A8A; font-weight: bold;
                                      text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
            .tarjeta-vivo .tv-val   { font-size: 28px; font-weight: bold; color: #2C4A5A; line-height: 1; }
            .tarjeta-vivo .tv-unit  { font-size: 11px; color: #5A7A8A; margin-top: 4px; }
            .tarjeta-vivo.tv-warn   { border-color: #F5A623; background: #FFFBF0; }
            .tarjeta-vivo.tv-crit   { border-color: #D65C5C; background: #FFF5F5; }
            .tarjeta-vivo.tv-crit .tv-val { color: #D65C5C; }

            .vivo-paciente-chip { background: #E3F2FA; border: 1px solid #7AAFC5; padding: 5px 14px;
                                  border-radius: 20px; font-size: 13px; font-weight: bold; color: #2C4A5A;
                                  display: inline-block; margin-bottom: 16px; }

            .charts-grid-vivo { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
            .chart-card-vivo { background: white; border: 1px solid #D4E8F3; border-radius: 8px;
                               padding: 8px; }  /* Padding reducido para eliminar espacios */
            .chart-card-vivo canvas { height: 170px !important; width: 100%; }

            /* ── Visor de Señales ── */
            .visor-layout { display: grid; grid-template-columns: 300px 1fr; gap: 20px; }
            .visor-panel { background: white; border-radius: 8px; border: 1px solid #D4E8F3; padding: 16px; }
            .visor-panel h3 { font-size: 12px; color: #5A7A8A; margin-bottom: 12px;
                              text-transform: uppercase; letter-spacing: 0.5px; }
            .visor-select { width: 100%; padding: 8px 10px; border: 1px solid #D4E8F3; border-radius: 4px;
                            font-size: 13px; background: #EEF5FB; color: #2C4A5A; margin-bottom: 10px; }
            .interr-list { max-height: 420px; overflow-y: auto; }
            .interr-item { padding: 10px 12px; border-radius: 6px; cursor: pointer; margin-bottom: 6px;
                           background: #EEF5FB; border: 1px solid #D4E8F3; transition: all 0.15s; }
            .interr-item:hover { border-color: #7AAFC5; background: #F0F8FF; }
            .interr-item.selected { border-color: #7AAFC5; background: #E3F2FA; }
            .interr-item .interr-title { font-weight: bold; font-size: 13px; color: #2C4A5A; }
            .interr-item .interr-meta { font-size: 11px; color: #5A7A8A; margin-top: 3px; }
            .charts-area { display: flex; flex-direction: column; gap: 16px; }
            .chart-card { background: white; border: 1px solid #D4E8F3; border-radius: 8px; padding: 16px; }
            .chart-card h4 { font-size: 12px; color: #5A7A8A; text-transform: uppercase;
                             letter-spacing: 0.5px; margin-bottom: 10px; }
            .no-signal { text-align: center; padding: 60px 20px; color: #5A7A8A; font-size: 13px; }
            .signal-count { font-size: 11px; background: #D4E8F3; color: #2C4A5A; padding: 2px 8px; border-radius: 10px; }
            .visor-info { background: #EEF5FB; border: 1px solid #D4E8F3; border-radius: 6px; padding: 12px;
                          margin-bottom: 14px; font-size: 12px; color: #5A7A8A;
                          display: flex; justify-content: space-between; align-items: center; }
            .visor-info strong { color: #2C4A5A; }
            .tabs-signal { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
            .tab-signal { padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: bold;
                          cursor: pointer; border: 2px solid transparent; background: #EEF5FB; color: #5A7A8A; }
            .tab-signal.active { color: white; }
            .tab-signal[data-tipo="frecuencia_respiratoria"].active { background: #4A9E6B; border-color: #4A9E6B; }
            .tab-signal[data-tipo="ecg"].active    { background: #E05C5C; border-color: #E05C5C; }
            .tab-signal[data-tipo="spo2"].active   { background: #5C9AE0; border-color: #5C9AE0; }
            .tab-signal[data-tipo="acce_z"].active { background: #5CBE80; border-color: #5CBE80; }
            .tab-signal[data-tipo="flujo"].active  { background: #E0A55C; border-color: #E0A55C; }
            .loading-msg { text-align: center; color: #7AAFC5; padding: 40px; font-size: 13px; }
            .fc-badge { background: #EEF8F2; color: #2E7D52; padding: 2px 8px; border-radius: 10px;
                        font-size: 11px; font-weight: bold; }
            .sel-row { display: flex; gap: 6px; align-items: center; margin-bottom: 10px; }
            .sel-row select { flex: 1; margin-bottom: 0; }

            /* ── Modales ── */
            .modal-bg { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                        background: rgba(44,74,90,0.35); z-index: 100; justify-content: center; align-items: center; }
            .modal-bg.show { display: flex; }
            .modal { background: white; border-radius: 8px; padding: 28px; width: 460px;
                     box-shadow: 0 10px 25px rgba(0,0,0,0.12); }
            .modal h2 { margin-bottom: 20px; font-size: 18px; }
            .form-group { margin-bottom: 14px; }
            .form-group label { display: block; font-size: 12px; color: #5A7A8A; margin-bottom: 4px; font-weight: bold; }
            .form-group input, .form-group select { width: 100%; padding: 8px 12px; border: 1px solid #D4E8F3;
                                                    border-radius: 4px; font-size: 13px; background: #EEF5FB; }
            .modal-confirm { background: white; border-radius: 8px; padding: 28px; width: 400px; text-align: center; }
            .modal-confirm h3 { color: #2C4A5A; margin-bottom: 10px; }
            .modal-confirm p { color: #5A7A8A; font-size: 13px; margin-bottom: 20px; line-height: 1.5; }
            .btn-row { display: flex; gap: 10px; justify-content: center; }

            /* ── Toast ── */
            .toast { position: fixed; bottom: 30px; right: 30px; background: #2C4A5A; color: white;
                     padding: 12px 24px; border-radius: 6px; display: none; z-index: 999; font-size: 14px; }
            .toast.show { display: block; }
        </style>
    </head>
    <body>

    <!-- HEADER -->
    <div class="banner">
        <h1>⚙️ AOS — Panel de Administración</h1>
        <a href="/logout" class="btn-logout">Cerrar Sesión</a>
    </div>

    <!-- NAVEGACIÓN -->
    <div class="tabs">
        <div class="tab active" onclick="cambiarTab('pacientes', this)">👥 Pacientes</div>
        <div class="tab" onclick="cambiarTab('usuarios', this)">🔑 Usuarios</div>
        <div class="tab" onclick="cambiarTab('monitoreo', this)">📊 Historial ESP32</div>
        <div class="tab" onclick="cambiarTab('vivo', this)">🟢 Señales en Vivo</div>
        <div class="tab" onclick="cambiarTab('senales', this)">📈 Visor de Señales</div>
    </div>

    <div class="content">

        <!-- ══ PACIENTES ══ -->
        <div id="sec-pacientes" class="section active">
            <div class="toolbar">
                <input class="search" id="buscar-pac" placeholder="🔍 Buscar paciente..." oninput="filtrarPacientes()">
                <button class="btn btn-primary" onclick="abrirModalPaciente()">+ Nuevo Paciente</button>
            </div>
            <div class="card-grid" id="grid-pacientes"></div>
        </div>

        <!-- ══ USUARIOS ══ -->
        <div id="sec-usuarios" class="section">
            <div class="toolbar">
                <span style="font-size:13px; color:#5A7A8A;">Gestión de usuarios</span>
                <button class="btn btn-primary" onclick="abrirModalUsuario()">+ Nuevo Usuario</button>
            </div>
            <table>
                <thead><tr><th>ID</th><th>Usuario</th><th>Acciones</th></tr></thead>
                <tbody id="tbody-usuarios"></tbody>
            </table>
        </div>

        <!-- ══ HISTORIAL ESP32 ══ -->
        <div id="sec-monitoreo" class="section">
            <div class="toolbar">
                <span style="font-size:13px; color:#5A7A8A;">Registros históricos enviados por el ESP32</span>
                <button class="btn btn-primary" onclick="cargarMonitoreo()">🔄 Actualizar</button>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Paciente</th><th>Hora</th><th>SpO2</th><th>ECG/FC</th>
                        <th>Acce Z</th><th>Flujo</th><th>N° Apnea</th><th>Duración</th>
                    </tr>
                </thead>
                <tbody id="tbody-monitoreo"></tbody>
            </table>
        </div>

        <!-- ══ SEÑALES EN VIVO ══ -->
        <div id="sec-vivo" class="section">
            <div class="vivo-header">
                <h2>🟢 Monitoreo en Vivo — ESP32</h2>
                <span class="ws-pill" id="ws-pill">
                    <span class="ws-dot" id="ws-dot"></span>
                    <span id="ws-label">Esperando equipo...</span>
                </span>
            </div>

            <!-- Chip del paciente activo -->
            <div id="vivo-paciente-wrap" style="display:none;">
                <span class="vivo-paciente-chip" id="vivo-paciente-chip">👤 —</span>
            </div>

            <!-- Indicadores numéricos: ECG, SpO2, Acce Z, Flujo -->
            <div class="indicadores-vivo">
                <div class="tarjeta-vivo" id="tv-ecg">
                    <div class="tv-label">ECG</div>
                    <div class="tv-val" id="val-ecg">--</div>
                    <div class="tv-unit">mV</div>
                </div>
                <div class="tarjeta-vivo" id="tv-spo2">
                    <div class="tv-label">SpO₂</div>
                    <div class="tv-val" id="val-spo2">--</div>
                    <div class="tv-unit">%</div>
                </div>
                <div class="tarjeta-vivo" id="tv-accz">
                    <div class="tv-label">Aceleración Z</div>
                    <div class="tv-val" id="val-accz">--</div>
                    <div class="tv-unit">m/s²</div>
                </div>
                <div class="tarjeta-vivo" id="tv-flujo">
                    <div class="tv-label">Flujo Resp.</div>
                    <div class="tv-val" id="val-flujo">--</div>
                    <div class="tv-unit">ADC</div>
                </div>
            </div>

            <!-- Gráficas 2×2 sin títulos y sin relleno -->
            <div class="charts-grid-vivo">
                <div class="chart-card-vivo">
                    <canvas id="chartECG"></canvas>
                </div>
                <div class="chart-card-vivo">
                    <canvas id="chartSPO2"></canvas>
                </div>
                <div class="chart-card-vivo">
                    <canvas id="chartACCZ"></canvas>
                </div>
                <div class="chart-card-vivo">
                    <canvas id="chartFLUJO"></canvas>
                </div>
            </div>
        </div>

        <!-- ══ VISOR DE SEÑALES ══ -->
        <div id="sec-senales" class="section">
            <div class="visor-layout">
                <!-- Panel izquierdo: navegación -->
                <div>
                    <div class="visor-panel">
                        <h3>📁 Navegación</h3>
                        <label style="font-size:11px;color:#5A7A8A;font-weight:bold;">PACIENTE</label>
                        <div class="sel-row">
                            <select class="visor-select" id="sel-paciente" onchange="onPacienteChange()" style="margin-bottom:0;">
                                <option value="">— Seleccionar —</option>
                            </select>
                        </div>
                        <label style="font-size:11px;color:#5A7A8A;font-weight:bold;">SESIÓN</label>
                        <div class="sel-row">
                            <select class="visor-select" id="sel-sesion" onchange="onSesionChange()" disabled style="margin-bottom:0;">
                                <option value="">— Seleccionar —</option>
                            </select>
                            <button class="btn-del-sm" id="btn-del-sesion" onclick="confirmarEliminarSesion()" disabled title="Eliminar sesión">🗑️</button>
                        </div>
                        <h3 style="margin-top:16px;">⚡ Apneas detectadas</h3>
                        <div id="interr-list" class="interr-list">
                            <p style="font-size:12px;color:#5A7A8A;text-align:center;padding:20px 0;">Selecciona un paciente y sesión</p>
                        </div>
                    </div>
                </div>
                <!-- Panel derecho: gráficas -->
                <div>
                    <div id="charts-placeholder" class="no-signal">
                        <div style="font-size:40px;margin-bottom:12px;">📈</div>
                        <p>Selecciona una apnea de la lista para visualizar sus señales</p>
                    </div>
                    <div id="charts-container" style="display:none;">
                        <div class="visor-info" id="interr-info">
                            <span id="interr-info-text"></span>
                            <button class="btn-del-sm" id="btn-del-apnea" onclick="confirmarEliminarApnea()" title="Eliminar apnea">🗑️ Eliminar apnea</button>
                        </div>
                        <div class="tabs-signal" id="tabs-signal"></div>
                        <div class="charts-area" id="charts-area"></div>
                    </div>
                </div>
            </div>
        </div>

    </div><!-- /content -->

    <!-- MODAL PACIENTE -->
    <div class="modal-bg" id="modal-paciente">
        <div class="modal">
            <h2 id="modal-pac-titulo">Paciente</h2>
            <input type="hidden" id="pac-id">
            <div class="form-group"><label>Nombre completo</label><input id="pac-nombre"></div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                <div class="form-group"><label>Fecha estudio</label><input id="pac-fecha" type="date"></div>
                <div class="form-group"><label>Edad</label><input id="pac-edad" type="number"></div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                <div class="form-group">
                    <label>Sexo</label>
                    <select id="pac-sexo">
                        <option value="Masculino">Masculino</option>
                        <option value="Femenino">Femenino</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Cardiovascular</label>
                    <select id="pac-cardio">
                        <option value="No">No</option>
                        <option value="Sí">Sí</option>
                    </select>
                </div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                <div class="form-group"><label>IMC</label><input id="pac-imc" type="number" step="0.1"></div>
                <div class="form-group"><label>EPWORTH</label><input id="pac-epworth" type="number"></div>
            </div>
            <div style="text-align:right; margin-top:10px;">
                <button class="btn" style="background:#EEE;color:#333;" onclick="cerrarModals()">Cancelar</button>
                <button class="btn btn-primary" onclick="guardarPaciente()">Guardar</button>
            </div>
        </div>
    </div>

    <!-- MODAL USUARIO -->
    <div class="modal-bg" id="modal-usuario">
        <div class="modal">
            <h2>Nuevo Usuario</h2>
            <div class="form-group"><label>Usuario (Login)</label><input id="usr-nombre"></div>
            <div class="form-group"><label>Contraseña</label><input id="usr-pass" type="password"></div>
            <div style="text-align:right;">
                <button class="btn" onclick="cerrarModals()">Cancelar</button>
                <button class="btn btn-primary" onclick="guardarUsuario()">Crear</button>
            </div>
        </div>
    </div>

    <!-- MODAL CONFIRMACIÓN -->
    <div class="modal-bg" id="modal-confirm">
        <div class="modal-confirm">
            <h3 id="confirm-titulo">¿Eliminar?</h3>
            <p id="confirm-texto"></p>
            <div class="btn-row">
                <button class="btn" style="background:#EEE;color:#333;" onclick="cerrarModals()">Cancelar</button>
                <button class="btn btn-danger" id="confirm-btn-ok">Sí, eliminar</button>
            </div>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        // ════════════════════════════════════════════
        // ESTADO GLOBAL
        // ════════════════════════════════════════════
        let pacientes = [];
        let chartInstances = {};
        let senalesCache = {};
        let apneaActivaId = null;
        let sesionActivaId = null;

        const SIGNAL_CONFIG = {
            ecg:    { label: 'ECG',                  color: '#E05C5C', bg: 'rgba(224,92,92,0.08)',   unit: 'mV',   emoji: '❤️'  },
            spo2:   { label: 'SpO₂',                 color: '#5C9AE0', bg: 'rgba(92,154,224,0.1)',   unit: '%',    emoji: '🩸'  },
            acce_z: { label: 'Aceleración Z',         color: '#5CBE80', bg: 'rgba(92,190,128,0.1)',   unit: 'm/s²', emoji: '🔵'  },
            flujo:  { label: 'Flujo resp.',           color: '#E0A55C', bg: 'rgba(224,165,92,0.1)',   unit: 'ADC',  emoji: '💨'  },
            frecuencia_respiratoria: {
                label: 'Flujo Respiratorio', color: '#4A9E6B', bg: 'rgba(74,158,107,0.12)', unit: 'ADC', emoji: '🌬️'
            },
        };

        // ════════════════════════════════════════════
        // UTILIDADES
        // ════════════════════════════════════════════
        function mostrarToast(msg) {
            const t = document.getElementById('toast');
            t.innerText = msg; t.classList.add('show');
            setTimeout(() => t.classList.remove('show'), 2800);
        }

        function cambiarTab(tab, el) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            el.classList.add('active');
            document.getElementById('sec-' + tab).classList.add('active');
            if (tab === 'pacientes') cargarPacientes();
            if (tab === 'usuarios')  cargarUsuarios();
            if (tab === 'monitoreo') cargarMonitoreo();
            if (tab === 'senales')   iniciarVisor();
        }

        function abrirConfirm(titulo, texto, fnConfirmar) {
            document.getElementById('confirm-titulo').textContent = titulo;
            document.getElementById('confirm-texto').textContent  = texto;
            const btn = document.getElementById('confirm-btn-ok');
            btn.onclick = () => { cerrarModals(); fnConfirmar(); };
            document.getElementById('modal-confirm').classList.add('show');
        }

        function cerrarModals() {
            document.querySelectorAll('.modal-bg').forEach(m => m.classList.remove('show'));
        }

        // ════════════════════════════════════════════
        // PACIENTES
        // ════════════════════════════════════════════
        async function cargarPacientes() {
            const res = await fetch('/pacientes');
            pacientes = await res.json();
            mostrarPacientes(pacientes);
        }

        function mostrarPacientes(datos) {
            const grid = document.getElementById('grid-pacientes');
            grid.innerHTML = '';
            datos.forEach(p => {
                const imcBadge = p.imc ? `<span class="badge ${p.imc >= 30 ? 'badge-crit' : 'badge-ok'}">${p.imc}</span>` : '--';
                const epwBadge = p.epworth != null ? `<span class="badge ${p.epworth >= 10 ? 'badge-warn' : 'badge-ok'}">${p.epworth}</span>` : '--';
                grid.innerHTML += `
                    <div class="card">
                        <h3>${p.nombre}</h3>
                        <p><strong>Fecha Estudio:</strong> ${p.fecha_estudio || 'N/A'}</p>
                        <p><strong>Edad:</strong> ${p.edad || '--'} años &nbsp;|&nbsp; <strong>Sexo:</strong> ${p.sexo || '--'}</p>
                        <p><strong>IMC:</strong> ${imcBadge} &nbsp;|&nbsp; <strong>Epworth:</strong> ${epwBadge}</p>
                        <p><strong>Enf. Cardio:</strong> ${p.enfermedad_cardiovascular || 'No'}</p>
                        <div class="card-actions">
                            <button class="btn btn-edit" onclick='editarPaciente(${JSON.stringify(p)})'>✏️ Editar</button>
                            <button class="btn btn-danger" onclick="confirmarEliminarPaciente(${p.id}, '${p.nombre.replace(/'/g, "\\'")}')">🗑️ Eliminar</button>
                        </div>
                    </div>`;
            });
        }

        function filtrarPacientes() {
            const q = document.getElementById('buscar-pac').value.toLowerCase();
            mostrarPacientes(pacientes.filter(p => p.nombre.toLowerCase().includes(q)));
        }

        function abrirModalPaciente() {
            document.getElementById('pac-id').value = '';
            ['pac-nombre','pac-fecha','pac-edad','pac-imc','pac-epworth'].forEach(id => document.getElementById(id).value = '');
            document.getElementById('modal-pac-titulo').innerText = 'Nuevo Paciente';
            document.getElementById('modal-paciente').classList.add('show');
        }

        function editarPaciente(p) {
            document.getElementById('pac-id').value = p.id;
            document.getElementById('pac-nombre').value = p.nombre;
            document.getElementById('pac-fecha').value = p.fecha_estudio || '';
            document.getElementById('pac-edad').value = p.edad || '';
            document.getElementById('pac-sexo').value = p.sexo || 'Masculino';
            document.getElementById('pac-cardio').value = p.enfermedad_cardiovascular || 'No';
            document.getElementById('pac-imc').value = p.imc || '';
            document.getElementById('pac-epworth').value = p.epworth || '';
            document.getElementById('modal-pac-titulo').innerText = 'Editar Paciente';
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
            await fetch(id ? '/pacientes/' + id : '/pacientes', {
                method: id ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            cerrarModals(); cargarPacientes(); mostrarToast('✅ Paciente guardado');
        }

        function confirmarEliminarPaciente(id, nombre) {
            abrirConfirm(
                '🗑️ Eliminar paciente',
                `Se eliminarán todos los datos de "${nombre}": sesiones, horas, apneas y señales. Esta acción no se puede deshacer.`,
                async () => {
                    const res = await fetch('/pacientes/' + id, { method: 'DELETE' });
                    if (res.ok) { mostrarToast('✅ Paciente eliminado'); cargarPacientes(); }
                    else mostrarToast('❌ Error al eliminar paciente');
                }
            );
        }

        // ════════════════════════════════════════════
        // USUARIOS
        // ════════════════════════════════════════════
        async function cargarUsuarios() {
            const res = await fetch('/usuarios');
            const data = await res.json();
            document.getElementById('tbody-usuarios').innerHTML = data.map(u => `
                <tr>
                    <td>${u.id}</td>
                    <td><strong>${u.usuario}</strong></td>
                    <td>${u.usuario !== 'admin'
                        ? `<button class="btn btn-danger" onclick="confirmarEliminarUsuario(${u.id}, '${u.usuario}')">🗑️ Eliminar</button>`
                        : '<span style="font-size:12px;color:#888;">Admin principal</span>'}</td>
                </tr>
            `).join('');
        }

        function abrirModalUsuario() {
            document.getElementById('usr-nombre').value = '';
            document.getElementById('usr-pass').value = '';
            document.getElementById('modal-usuario').classList.add('show');
        }

        async function guardarUsuario() {
            await fetch('/usuarios', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    usuario: document.getElementById('usr-nombre').value,
                    contrasena: document.getElementById('usr-pass').value
                })
            });
            cerrarModals(); cargarUsuarios(); mostrarToast('✅ Usuario creado');
        }

        function confirmarEliminarUsuario(id, nombre) {
            abrirConfirm(
                '🗑️ Eliminar usuario',
                `¿Eliminar de forma permanente el acceso para "${nombre}"?`,
                async () => {
                    const res = await fetch('/usuarios/' + id, { method: 'DELETE' });
                    if (res.ok) { mostrarToast('✅ Usuario eliminado'); cargarUsuarios(); }
                    else mostrarToast('❌ Error al eliminar usuario');
                }
            );
        }

        // ════════════════════════════════════════════
        // HISTORIAL ESP32
        // ════════════════════════════════════════════
        async function cargarMonitoreo() {
            const res = await fetch('/datos-sensores');
            const datos = await res.json();
            if (datos.error) {
                document.getElementById('tbody-monitoreo').innerHTML =
                    `<tr><td colspan="8" style="color:red;text-align:center;">Error: ${datos.error}</td></tr>`;
                return;
            }
            if (!datos.length) {
                document.getElementById('tbody-monitoreo').innerHTML =
                    '<tr><td colspan="8" style="text-align:center;color:#888;">No hay datos registrados</td></tr>';
                return;
            }
            document.getElementById('tbody-monitoreo').innerHTML = datos.map(d => `
                <tr>
                    <td><strong>${d.paciente_nombre}</strong></td>
                    <td>${d.hora_detectada || '--'}</td>
                    <td><span class="badge ${d.spo2 < 90 ? 'badge-crit' : 'badge-ok'}">${d.spo2}%</span></td>
                    <td>${d.ecg}</td>
                    <td>${d.acce_z || 0}</td>
                    <td>${d.flujo || 0}</td>
                    <td>${d.numero_apnea}</td>
                    <td>${d.duracion_apnea}s</td>
                </tr>
            `).join('');
        }

        // ════════════════════════════════════════════
        // SEÑALES EN VIVO — WebSocket + /stream
        // El ESP32 hace POST a /stream cada 200 ms;
        // la API difunde ese JSON por WS a /ws/browser.
        // Formato recibido: { tipo:"stream", paciente, timestamp_ms, ecg, spo2, acce_z, flujo }
        // ════════════════════════════════════════════
        const MAX_PUNTOS = 150;   // ventana deslizante de 30 s a 200 ms/muestra

        // ── Crear una gráfica en vivo sin relleno, con eje Y fijo para ECG ──
        function crearChartVivo(canvasId, color, yLabel, minY = null, maxY = null, fillEnabled = false) {
            const ctx = document.getElementById(canvasId).getContext('2d');
            const options = {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                layout: {
                    padding: { top: 2, bottom: 2, left: 2, right: 2 }  // reducir espacios internos
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { enabled: false }
                },
                scales: {
                    x: { display: false },
                    y: {
                        grid: { color: '#EEF5FB' },
                        ticks: { font: { size: 10 }, color: '#5A7A8A', maxTicksLimit: 4 },
                        title: { display: false }
                    }
                }
            };
            if (minY !== null && maxY !== null) {
                options.scales.y.min = minY;
                options.scales.y.max = maxY;
            }
            return new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        data: [],
                        borderColor: color,
                        backgroundColor: fillEnabled ? color.replace(')', ', 0.08)').replace('rgb', 'rgba') : 'transparent',
                        borderWidth: 1.6,
                        tension: 0.35,
                        pointRadius: 0,
                        fill: fillEnabled,
                    }]
                },
                options: options
            });
        }

        // Inicializar las 4 gráficas en vivo (sin relleno, ECG con eje fijo -50..50)
        const chartVivo = {
            ecg:    crearChartVivo('chartECG',   '#E05C5C', 'mV', -50, 50, false),
            spo2:   crearChartVivo('chartSPO2',  '#5C9AE0', '%',   null, null, false),
            acce_z: crearChartVivo('chartACCZ',  '#5CBE80', 'm/s²', null, null, false),
            flujo:  crearChartVivo('chartFLUJO', '#E0A55C', 'ADC', null, null, false),
        };

        // ── Agregar un punto a una gráfica, manteniendo ventana deslizante ────────
        function pushPunto(chart, label, valor) {
            chart.data.labels.push(label);
            chart.data.datasets[0].data.push(valor);
            if (chart.data.labels.length > MAX_PUNTOS) {
                chart.data.labels.shift();
                chart.data.datasets[0].data.shift();
            }
            chart.update('none');   // sin animación para máxima fluidez
        }

        // ── Actualizar indicadores numéricos con color según umbral ───────────────
        function actualizarIndicador(idVal, idCard, valor, unidad, umbralWarn, umbralCrit, invertir) {
            const el = document.getElementById(idVal);
            const card = document.getElementById(idCard);
            if (el) el.textContent = typeof valor === 'number' ? valor.toFixed(unidad === '%' ? 0 : 1) : '--';
            if (!card) return;
            card.classList.remove('tv-warn', 'tv-crit');
            if (valor === null) return;
            const critico = invertir ? valor < umbralCrit : valor > umbralCrit;
            const advertencia = invertir ? valor < umbralWarn : valor > umbralWarn;
            if (critico)      card.classList.add('tv-crit');
            else if (advertencia) card.classList.add('tv-warn');
        }

        // ── WebSocket al servidor ─────────────────────────────────────────────────
        const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
        let ws;

        function conectarWS() {
            ws = new WebSocket(wsProtocol + window.location.host + '/ws/browser');

            ws.onopen = () => {
                document.getElementById('ws-dot').className   = 'ws-dot on';
                document.getElementById('ws-label').textContent = 'Conectado — esperando datos...';
            };

            ws.onclose = () => {
                document.getElementById('ws-dot').className   = 'ws-dot off';
                document.getElementById('ws-label').textContent = 'Sin conexión — reconectando...';
                setTimeout(conectarWS, 3000);   // reconexión automática
            };

            ws.onerror = () => ws.close();

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

                    // Solo procesar mensajes de tipo "stream" del ESP32
                    if (data.tipo !== 'stream') return;

                    const ts = new Date().toLocaleTimeString('es-MX', { hour12: false });

                    // ── Chip de paciente ─────────────────────────────────────────
                    if (data.paciente) {
                        const wrap = document.getElementById('vivo-paciente-wrap');
                        const chip = document.getElementById('vivo-paciente-chip');
                        wrap.style.display = 'block';
                        chip.textContent = '👤 ' + data.paciente;
                    }

                    // ── Estado WS activo ─────────────────────────────────────────
                    document.getElementById('ws-dot').className   = 'ws-dot on';
                    document.getElementById('ws-label').textContent = 'Transmitiendo en vivo';

                    // ── ECG ───────────────────────────────────────────────────────
                    if (data.ecg !== undefined) {
                        pushPunto(chartVivo.ecg, ts, data.ecg);
                        document.getElementById('val-ecg').textContent = parseFloat(data.ecg).toFixed(1);
                    }

                    // ── SpO2: crítico < 90, advertencia < 95 ─────────────────────
                    if (data.spo2 !== undefined) {
                        pushPunto(chartVivo.spo2, ts, data.spo2);
                        actualizarIndicador('val-spo2', 'tv-spo2', data.spo2, '%', 95, 90, true);
                    }

                    // ── Aceleración Z ─────────────────────────────────────────────
                    if (data.acce_z !== undefined) {
                        pushPunto(chartVivo.acce_z, ts, data.acce_z);
                        document.getElementById('val-accz').textContent = parseFloat(data.acce_z).toFixed(3);
                    }

                    // ── Flujo respiratorio ────────────────────────────────────────
                    if (data.flujo !== undefined) {
                        pushPunto(chartVivo.flujo, ts, data.flujo);
                        document.getElementById('val-flujo').textContent = parseInt(data.flujo);
                    }

                } catch(e) {
                    console.warn('[WS] Error al parsear:', e);
                }
            };
        }

        conectarWS();

        // ════════════════════════════════════════════
        // VISOR DE SEÑALES
        // ════════════════════════════════════════════
        async function iniciarVisor() {
            const res = await fetch('/pacientes');
            const pacs = await res.json();
            const sel = document.getElementById('sel-paciente');
            sel.innerHTML = '<option value="">— Seleccionar —</option>';
            pacs.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.id; opt.textContent = p.nombre;
                sel.appendChild(opt);
            });
        }

        async function onPacienteChange() {
            const pacId = document.getElementById('sel-paciente').value;
            const selSes = document.getElementById('sel-sesion');
            const btnDel = document.getElementById('btn-del-sesion');
            selSes.innerHTML = '<option value="">— Seleccionar —</option>';
            selSes.disabled = true; btnDel.disabled = true;
            sesionActivaId = null;
            document.getElementById('interr-list').innerHTML =
                '<p style="font-size:12px;color:#5A7A8A;text-align:center;padding:20px 0;">Selecciona una sesión</p>';
            resetCharts();
            if (!pacId) return;
            const res = await fetch('/sesiones/por-paciente/' + pacId);
            const sesiones = await res.json();
            sesiones.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.id;
                opt.textContent = 'Sesión #' + s.id + ' — ' + (s.fecha || '').substring(0, 16);
                selSes.appendChild(opt);
            });
            selSes.disabled = false;
        }

        async function onSesionChange() {
            const sesId = document.getElementById('sel-sesion').value;
            const btnDel = document.getElementById('btn-del-sesion');
            sesionActivaId = sesId ? parseInt(sesId) : null;
            btnDel.disabled = !sesId;
            resetCharts();
            if (!sesId) {
                document.getElementById('interr-list').innerHTML =
                    '<p style="font-size:12px;color:#5A7A8A;text-align:center;padding:20px 0;">Selecciona una sesión</p>';
                return;
            }
            document.getElementById('interr-list').innerHTML = '<div class="loading-msg">Cargando apneas...</div>';
            const resHoras = await fetch('/horas-sesion/' + sesId);
            const horas = await resHoras.json();
            window._horaOrdenMap = {};
            horas.forEach(h => { window._horaOrdenMap[h.numero_hora] = h.hora_orden; });
            const res = await fetch('/interrupciones-sesion/' + sesId);
            const interrupciones = await res.json();
            renderInterrList(interrupciones);
        }

        function renderInterrList(interrupciones) {
            const cont = document.getElementById('interr-list');
            if (!interrupciones.length) {
                cont.innerHTML = '<p style="font-size:12px;color:#5A7A8A;text-align:center;padding:20px 0;">No hay apneas registradas en esta sesión</p>';
                return;
            }
            cont.innerHTML = interrupciones.map((i, globalIdx) => {
                const spo2Class = i.spo2 < 90 ? 'badge-crit' : i.spo2 < 95 ? 'badge-warn' : 'badge-ok';
                const tieneSenales = i.total_senales > 0;
                const numApnea = i.numero_consecutivo || (globalIdx + 1);
                const horaOrden = (window._horaOrdenMap && window._horaOrdenMap[i.numero_hora]) || i.numero_hora;
                return `
                <div class="interr-item" id="item-${i.id}" onclick="cargarSenales(${i.id}, this)">
                    <div class="interr-title">Apnea #${numApnea} · Hora ${horaOrden}</div>
                    <div class="interr-meta">🕐 ${i.hora_detectada || '--'} &nbsp;|&nbsp; ⏱️ ${i.duracion_segundos}s</div>
                    <div style="margin-top:5px;">
                        <span class="badge ${spo2Class}">SpO₂ ${i.spo2}%</span>
                        &nbsp;
                        ${tieneSenales
                            ? `<span class="signal-count">📶 ${i.total_senales} muestras</span>`
                            : `<span class="badge badge-crit">Sin señales</span>`}
                    </div>
                </div>`;
            }).join('');
        }

        async function cargarSenales(interrupcionId, el) {
            document.querySelectorAll('.interr-item').forEach(i => i.classList.remove('selected'));
            el.classList.add('selected');
            apneaActivaId = interrupcionId;
            document.getElementById('charts-placeholder').style.display = 'none';
            document.getElementById('charts-container').style.display = 'block';
            document.getElementById('charts-area').innerHTML = '<div class="loading-msg">⏳ Cargando señales...</div>';
            const titulo = el.querySelector('.interr-title').textContent;
            const meta   = el.querySelector('.interr-meta').textContent;
            document.getElementById('interr-info-text').innerHTML =
                '<strong>' + titulo + '</strong> &nbsp;·&nbsp; ' + meta;
            let data = senalesCache[interrupcionId];
            if (!data) {
                try {
                    const res = await fetch('/senales-completas/' + interrupcionId);
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    data = await res.json();
                    senalesCache[interrupcionId] = data;
                } catch (err) {
                    document.getElementById('charts-area').innerHTML =
                        `<div class="no-signal" style="padding:40px;">❌ Error al cargar señales: ${err.message}</div>`;
                    return;
                }
            }
            const tipos = Object.keys(data);
            if (!tipos.length) {
                document.getElementById('charts-area').innerHTML =
                    '<div class="no-signal" style="padding:40px;">⚠️ Esta apnea no tiene señales almacenadas.</div>';
                document.getElementById('tabs-signal').innerHTML = '';
                return;
            }
            renderTabsSignal(tipos, data);
        }

        function confirmarEliminarApnea() {
            if (!apneaActivaId) return;
            const idAEliminar = apneaActivaId;
            abrirConfirm(
                '🗑️ Eliminar apnea',
                'Se eliminarán la apnea y todas sus señales almacenadas. Esta acción no se puede deshacer.',
                async () => {
                    const res = await fetch('/interrupciones/' + idAEliminar, { method: 'DELETE' });
                    if (res.ok) {
                        mostrarToast('✅ Apnea eliminada');
                        delete senalesCache[idAEliminar];
                        apneaActivaId = null;
                        resetCharts();
                        const sesId = document.getElementById('sel-sesion').value;
                        if (sesId) onSesionChange();
                    } else { mostrarToast('❌ Error al eliminar apnea'); }
                }
            );
        }

        function confirmarEliminarSesion() {
            if (!sesionActivaId) return;
            const sel = document.getElementById('sel-sesion');
            const sesText = sel.options[sel.selectedIndex].text;
            abrirConfirm(
                '🗑️ Eliminar sesión',
                `Se eliminarán "${sesText}" y todos sus datos: horas, apneas y señales. Esta acción no se puede deshacer.`,
                async () => {
                    const res = await fetch('/sesiones/' + sesionActivaId, { method: 'DELETE' });
                    if (res.ok) {
                        mostrarToast('✅ Sesión eliminada');
                        sesionActivaId = null; apneaActivaId = null; senalesCache = {};
                        resetCharts();
                        const pacId = document.getElementById('sel-paciente').value;
                        if (pacId) onPacienteChange();
                    } else { mostrarToast('❌ Error al eliminar sesión'); }
                }
            );
        }

        function renderTabsSignal(tipos, data) {
            const orden = ['frecuencia_respiratoria', 'ecg', 'spo2', 'acce_z', 'flujo'];
            const tiposOrdenados = orden.filter(t => tipos.includes(t))
                .concat(tipos.filter(t => !orden.includes(t)));
            const tabsCont = document.getElementById('tabs-signal');
            tabsCont.innerHTML = tiposOrdenados.map((tipo, idx) => {
                const cfg = SIGNAL_CONFIG[tipo] || { label: tipo, emoji: '📶' };
                const n = data[tipo] ? data[tipo].timestamps.length : 0;
                return `<span class="tab-signal ${idx===0?'active':''}" data-tipo="${tipo}">
                    ${cfg.emoji} ${cfg.label} <span style="opacity:0.7;font-size:10px;">(${n})</span>
                </span>`;
            }).join('');
            window._signalData  = data;
            window._signalTipos = tiposOrdenados;
            mostrarChartTipo(tiposOrdenados[0], data);
            document.querySelectorAll('.tab-signal').forEach(tab => {
                tab.onclick = () => {
                    document.querySelectorAll('.tab-signal').forEach(t => t.classList.remove('active'));
                    tab.classList.add('active');
                    mostrarChartTipo(tab.dataset.tipo, window._signalData);
                };
            });
        }

        function calcularFC(timestamps, valores) {
            if (timestamps.length < 20) return null;
            const duracionTotal = (timestamps[timestamps.length - 1] - timestamps[0]) / 1000.0;
            if (duracionTotal <= 0) return null;
            const fs = timestamps.length / duracionTotal;
            const diff = [];
            for (let i = 1; i < valores.length; i++) diff.push(valores[i] - valores[i - 1]);
            const sq = diff.map(v => v * v);
            const winSamples = Math.max(3, Math.round(fs * 0.15));
            const integrated = [];
            for (let i = 0; i < sq.length; i++) {
                let sum = 0, count = 0;
                for (let j = Math.max(0, i - winSamples); j <= i; j++) { sum += sq[j]; count++; }
                integrated.push(sum / count);
            }
            const media = integrated.reduce((a, b) => a + b, 0) / integrated.length;
            const umbral = media * 0.5;
            const refractario = Math.round(fs * 0.2);
            let picos = 0, ultimoPico = -refractario, enPico = false;
            for (let i = 1; i < integrated.length - 1; i++) {
                if (integrated[i] > umbral) {
                    if (!enPico && (i - ultimoPico) >= refractario &&
                        integrated[i] >= integrated[i - 1] && integrated[i] >= integrated[i + 1]) {
                        picos++; ultimoPico = i; enPico = true;
                    }
                } else { enPico = false; }
            }
            if (picos < 2) return null;
            const fc = Math.round((picos / duracionTotal) * 60);
            return (fc >= 30 && fc <= 220) ? fc : null;
        }

        function detectarYFiltrarRuido(timestamps, valores) {
            if (timestamps.length < 50) return { valores, filtrado: false };
            const duracion = (timestamps[timestamps.length - 1] - timestamps[0]) / 1000.0;
            const fs = timestamps.length / duracion;
            const energia60 = (() => {
                let sumSin = 0, sumCos = 0, sumTotal = 0;
                const f0 = 60;
                for (let i = 0; i < valores.length; i++) {
                    const t = (timestamps[i] - timestamps[0]) / 1000.0;
                    const v = valores[i];
                    sumSin += v * Math.sin(2 * Math.PI * f0 * t);
                    sumCos += v * Math.cos(2 * Math.PI * f0 * t);
                    sumTotal += v * v;
                }
                const pot60 = (sumSin * sumSin + sumCos * sumCos) / (valores.length * valores.length);
                const potTotal = sumTotal / valores.length;
                return potTotal > 0 ? pot60 / potTotal : 0;
            })();
            if (energia60 < 0.35) return { valores, filtrado: false };
            const r = 0.95;
            const omega = 2 * Math.PI * 60 / fs;
            const cosOmega = Math.cos(omega);
            const b0 = 1, b1 = -2 * cosOmega, b2 = 1;
            const a1 = -2 * r * cosOmega, a2 = r * r;
            const filtrado = new Array(valores.length).fill(0);
            for (let i = 0; i < valores.length; i++) {
                const x0 = valores[i];
                const x1 = i >= 1 ? valores[i - 1] : 0;
                const x2 = i >= 2 ? valores[i - 2] : 0;
                const y1 = i >= 1 ? filtrado[i - 1] : 0;
                const y2 = i >= 2 ? filtrado[i - 2] : 0;
                filtrado[i] = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
            }
            return { valores: filtrado, filtrado: true };
        }

        function mostrarChartTipo(tipo, data) {
            const area = document.getElementById('charts-area');
            const cfg  = SIGNAL_CONFIG[tipo] || { label: tipo, color: '#7AAFC5', bg: 'rgba(122,175,197,0.1)', unit: '', emoji: '📶' };
            const señal = data[tipo];
            if (!señal || !señal.timestamps.length) {
                area.innerHTML = `<div class="no-signal">Sin datos para ${cfg.label}</div>`;
                return;
            }
            let valoresGrafica = señal.valores;
            let filtroAplicado = false;
            if (tipo === 'ecg' && señal.timestamps.length > 0) {
                const resultado = detectarYFiltrarRuido(señal.timestamps, señal.valores);
                valoresGrafica = resultado.valores;
                filtroAplicado = resultado.filtrado;
            }
            if (chartInstances[tipo]) { chartInstances[tipo].destroy(); delete chartInstances[tipo]; }
            const canvasId = 'chart-' + tipo;
            const vMin = Math.min(...señal.valores);
            const vMax = Math.max(...señal.valores);
            let extraHtml = '';
            if (tipo === 'frecuencia_respiratoria') {
                const durSeg = señal.timestamps.length > 1
                    ? ((señal.timestamps[señal.timestamps.length-1] - señal.timestamps[0]) / 1000).toFixed(1) : '--';
                extraHtml = `<div style="margin-bottom:8px;font-size:11px;color:#5A7A8A;">
                    Señal respiratoria combinada (flujo + aceleración Z) &nbsp;·&nbsp;
                    Duración: ${durSeg}s &nbsp;·&nbsp; Rango: ${vMin.toFixed(0)} – ${vMax.toFixed(0)} ADC</div>`;
            }
            if (tipo === 'ecg') {
                const fc = calcularFC(señal.timestamps, valoresGrafica);
                const fcHtml = fc ? `<span class="fc-badge">❤️ ${fc} lpm</span>` : '';
                const filtroHtml = filtroAplicado
                    ? `<span style="font-size:11px;background:#FFF8EC;color:#B07020;padding:2px 8px;border-radius:10px;margin-left:8px;">⚡ Filtro 60Hz activo</span>` : '';
                extraHtml = `<div style="margin-bottom:10px;">
                    <span style="font-size:12px;color:#5A7A8A;">Frecuencia cardiaca estimada: </span>${fcHtml}${filtroHtml}</div>`;
            }
            const statsHtml = `
                <div style="display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:4px;">
                    ${statBox('Mínimo', vMin.toFixed(2), cfg.unit)}
                    ${statBox('Máximo', vMax.toFixed(2), cfg.unit)}
                    ${statBox('Promedio', (señal.valores.reduce((a,b)=>a+b,0)/señal.valores.length).toFixed(2), cfg.unit)}
                </div>`;
            area.innerHTML = `
                <div class="chart-card">
                    <h4>${cfg.emoji} ${cfg.label} — ${señal.timestamps.length} muestras</h4>
                    ${extraHtml}
                    <canvas id="${canvasId}" height="160"></canvas>
                </div>${statsHtml}`;
            const ctx = document.getElementById(canvasId).getContext('2d');
            const t0 = señal.timestamps[0];
            const labels = señal.timestamps.map(t => ((t - t0) / 1000).toFixed(2) + 's');
            const esResp  = tipo === 'frecuencia_respiratoria';
            const esEcg   = tipo === 'ecg';
            const esAccz  = tipo === 'acce_z';
            const esFlujo = tipo === 'flujo';
            const tension = esEcg ? 0.15 : (esResp ? 0.5 : (esAccz ? 0.45 : (esFlujo ? 0.4 : 0.3)));
            const pad = esEcg ? Math.max((vMax - vMin) * 0.1, 5) : Math.max((vMax - vMin) * 0.1, 1);
            const yScaleOpts = {
                min: vMin - pad, max: vMax + pad,
                ticks: { font: { size: 10 }, color: '#5A7A8A', maxTicksLimit: 6,
                         callback: (v) => esEcg ? v.toFixed(0) : v.toFixed(1) },
                grid: { color: '#EEF5FB' }
            };
            const ocultarPuntos = señal.timestamps.length > 100 || esResp || esAccz || esFlujo;
            chartInstances[tipo] = new Chart(ctx, {
                type: 'line',
                data: {
                    labels,
                    datasets: [{
                        label: cfg.label, data: valoresGrafica,
                        borderColor: cfg.color, backgroundColor: cfg.bg,
                        borderWidth: esEcg ? 1.2 : (esResp ? 2.5 : 1.6),
                        pointRadius: ocultarPuntos ? 0 : 2, pointHoverRadius: 4,
                        fill: !esEcg, tension, cubicInterpolationMode: 'monotone',
                    }]
                },
                options: {
                    responsive: true, animation: { duration: 300 },
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(2)} ${cfg.unit}` } }
                    },
                    scales: {
                        x: { ticks: { maxTicksLimit: 12, font: { size: 10 }, color: '#5A7A8A' },
                             grid: { color: '#EEF5FB' },
                             title: { display: true, text: 'Tiempo (s)', font: { size: 10 }, color: '#5A7A8A' } },
                        y: yScaleOpts
                    }
                }
            });
        }

        function statBox(label, val, unit) {
            return `<div style="background:#EEF5FB;border:1px solid #D4E8F3;border-radius:6px;padding:10px;text-align:center;">
                <div style="font-size:10px;color:#5A7A8A;margin-bottom:4px;">${label}</div>
                <div style="font-size:16px;font-weight:bold;color:#2C4A5A;">${val}</div>
                <div style="font-size:10px;color:#5A7A8A;">${unit}</div>
            </div>`;
        }

        function resetCharts() {
            Object.values(chartInstances).forEach(c => c.destroy());
            chartInstances = {};
            document.getElementById('charts-placeholder').style.display = 'block';
            document.getElementById('charts-container').style.display = 'none';
            document.getElementById('tabs-signal').innerHTML = '';
            document.getElementById('charts-area').innerHTML = '';
            apneaActivaId = null;
        }

        // ── Inicio ──
        window.onload = cargarPacientes;
    </script>
    </body>
    </html>
    """
