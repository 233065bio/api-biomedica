from fastapi import FastAPI, HTTPException, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import mysql.connector
import os
import bcrypt
import json
import math as _math

app = FastAPI()

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "aos_db"),
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
        
        # Tablas de Usuarios y Pacientes (de main caro)
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
        
        # Tablas del Historial ESP32 (de main 2)
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

class LoginRequest(BaseModel):
    usuario: str
    contrasena: str

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

class AnotacionModel(BaseModel):
    anotacion: str

# Helper para conversiones de tiempo
def timedelta_a_str(valor):
    if valor is None: return None
    if hasattr(valor, 'total_seconds'):
        total = int(valor.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return str(valor)

# ─────────────────────────────────────────────
# WEBSOCKETS (SEÑALES EN VIVO - de main caro)
# ─────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str, sender: WebSocket):
        for connection in self.active_connections:
            if connection != sender:
                try:
                    await connection.send_text(message)
                except Exception:
                    pass

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.broadcast(data, sender=websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────
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

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response

# ─────────────────────────────────────────────
# PACIENTES Y USUARIOS (de main caro)
# ─────────────────────────────────────────────
@app.get("/api/pacientes")
def get_pacientes():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM pacientes ORDER BY id DESC")
        rows = cursor.fetchall()
        for r in rows:
            if r["fecha_estudio"]: r["fecha_estudio"] = str(r["fecha_estudio"])
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pacientes")
def create_paciente(pac: PacienteModel):
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
        return {"id": pac_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/pacientes/{paciente_id}")
def delete_paciente(paciente_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Eliminar en cascada
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
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/usuarios")
def get_usuarios():
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
def create_usuario(usr: UsuarioModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        hashed = hash_password(usr.contrasena)
        cursor.execute("INSERT INTO usuarios (usuario, contrasena) VALUES (%s, %s)", (usr.usuario, hashed))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# HISTORIAL ESP32 Y SESIONES (de main 2)
# ─────────────────────────────────────────────
@app.get("/sesiones/por-paciente/{paciente_id}")
def sesiones_por_paciente(paciente_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, fecha FROM sesiones
            WHERE paciente_id = %s
            ORDER BY fecha DESC
        """, (paciente_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        for r in rows:
            r["fecha"] = str(r["fecha"]) if r.get("fecha") else None
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/horas-sesion/{sesion_id}")
def horas_sesion_endpoint(sesion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT hs.id, hs.numero_hora, hs.hora_inicio, hs.hora_fin,
                   COUNT(i.id) AS total_interrupciones
            FROM horas_sesion hs
            LEFT JOIN interrupciones i ON i.hora_sesion_id = hs.id
            WHERE hs.sesion_id = %s
            GROUP BY hs.id
            ORDER BY hs.numero_hora
        """, (sesion_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        for idx, r in enumerate(rows):
            r["hora_inicio"] = timedelta_a_str(r.get("hora_inicio"))
            r["hora_fin"]    = timedelta_a_str(r.get("hora_fin"))
            r["hora_orden"]  = idx + 1
            r["hora_real"]   = r["numero_hora"]
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/interrupciones-sesion/{sesion_id}")
def interrupciones_por_sesion(sesion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT i.id, i.numero_interrupcion, i.hora_detectada,
                   i.duracion_segundos, i.spo2, i.frecuencia_cardiaca,
                   i.anotacion, hs.numero_hora
            FROM interrupciones i
            JOIN horas_sesion hs ON i.hora_sesion_id = hs.id
            WHERE hs.sesion_id = %s
            ORDER BY i.id
        """, (sesion_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        for global_idx, r in enumerate(rows):
            r["hora_detectada"] = timedelta_a_str(r.get("hora_detectada"))
            r["numero_consecutivo"] = global_idx + 1
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/interrupciones/{interrupcion_id}/anotacion")
async def guardar_anotacion_endpoint(interrupcion_id: int, body: AnotacionModel):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE interrupciones SET anotacion=%s WHERE id=%s",
            (body.anotacion, interrupcion_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# VISOR DE SEÑALES (de main 2)
# ─────────────────────────────────────────────
@app.get("/senales-completas/{interrupcion_id}")
def senales_completas(interrupcion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT tipo_senal, timestamp_ms, valor
            FROM senales_esp32
            WHERE interrupcion_id = %s
            ORDER BY tipo_senal, timestamp_ms
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

        # Procesar ECG primero para tener referencia temporal y limpiar outliers
        for tipo, data in raw.items():
            ts = data["timestamps"]
            vs = data["valores"]
            if tipo.lower() == "ecg":
                ts, vs = _limpiar_outliers_ecg(ts, vs)
                ts_ecg_ref = ts 
            resultado[tipo] = {"timestamps": ts, "valores": vs}

        # Construir señal de frecuencia respiratoria
        ts_flujo = raw.get("flujo", {}).get("timestamps", [])
        vs_flujo = raw.get("flujo", {}).get("valores", [])
        ts_accz = raw.get("acce_z", {}).get("timestamps", [])
        vs_accz = raw.get("acce_z", {}).get("valores", [])
        
        ts_resp, vs_resp = _construir_resp_desde_streaming(
            ts_flujo, vs_flujo, ts_accz, vs_accz, ts_ecg_ref
        )
        
        if ts_resp and vs_resp:
            resultado["frecuencia_respiratoria"] = {
                "timestamps": ts_resp,
                "valores": vs_resp
            }

        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _limpiar_outliers_ecg(timestamps, valores):
    """Limpieza de outliers para señales de ECG (Preservando los Picos R)"""
    if len(valores) < 10:
        return timestamps, valores
    sorted_v = sorted(valores)
    n = len(sorted_v)
    q1 = sorted_v[n // 4]
    q3 = sorted_v[(3 * n) // 4]
    iqr = q3 - q1
    if iqr == 0:
        return timestamps, valores
    mediana = sorted_v[n // 2]
    # Limites holgados para no borrar los picos R
    lim_inf = mediana - 8 * iqr
    lim_sup = mediana + 8 * iqr
    valores_limpios = [max(lim_inf, min(lim_sup, v)) for v in valores]
    return timestamps, valores_limpios

def _construir_resp_desde_streaming(ts_flujo, vs_flujo, ts_accz, vs_accz, ts_ecg_ref=None):
    """Fusión y construcción para deducir la frecuencia respiratoria"""
    # Si tenemos señal de flujo la usamos, sino intentamos con accz o usamos fallback
    if ts_flujo and vs_flujo:
        return ts_flujo, vs_flujo
    elif ts_accz and vs_accz:
        return ts_accz, vs_accz
    elif ts_ecg_ref:
        # Generar una señal en blanco (o plana) de referencia si no existen las otras
        return ts_ecg_ref, [0] * len(ts_ecg_ref)
    return [], []
