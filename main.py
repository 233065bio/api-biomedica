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

def timedelta_a_str(valor):
    if valor is None: return None
    if hasattr(valor, 'total_seconds'):
        total = int(valor.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return str(valor)

# ─────────────────────────────────────────────
# WEBSOCKETS (TRANSMISIÓN EN TIEMPO REAL)
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
    except Exception as e:
        manager.disconnect(websocket)

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

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response

# ─────────────────────────────────────────────
# ENDPOINTS BÁSICOS: PACIENTES Y USUARIOS
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

@app.put("/pacientes/{paciente_id}")
def update_paciente(paciente_id: int, pac: PacienteModel):
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
def delete_paciente(paciente_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # ELIMINAR EN CASCADA TODO LO DEL PACIENTE
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

@app.delete("/usuarios/{usr_id}")
def delete_usuario(usr_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM usuarios WHERE id=%s", (usr_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ENDPOINTS SESIONES Y HORAS
# ─────────────────────────────────────────────
@app.get("/sesiones/por-paciente/{paciente_id}")
def sesiones_por_paciente(paciente_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, fecha FROM sesiones WHERE paciente_id = %s ORDER BY fecha DESC
        """, (paciente_id,))
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

# ─────────────────────────────────────────────
# ENDPOINTS INTERRUPCIONES Y SEÑALES
# ─────────────────────────────────────────────
@app.get("/interrupciones-sesion/{sesion_id}")
def interrupciones_por_sesion(sesion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT i.id, i.numero_interrupcion, i.hora_detectada, i.duracion_segundos, i.spo2, i.frecuencia_cardiaca,
                   i.anotacion, hs.numero_hora, (SELECT COUNT(*) FROM senales_esp32 WHERE interrupcion_id = i.id) AS total_senales
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

@app.get("/senales-completas/{interrupcion_id}")
def senales_completas(interrupcion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT tipo_senal, timestamp_ms, valor FROM senales_esp32 WHERE interrupcion_id = %s ORDER BY tipo_senal, timestamp_ms
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

def _limpiar_outliers_ecg(timestamps, valores):
    if len(valores) < 10: return timestamps, valores
    sorted_v = sorted(valores)
    n = len(sorted_v)
    iqr = sorted_v[(3 * n) // 4] - sorted_v[n // 4]
    if iqr == 0: return timestamps, valores
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
    
    if ts_ecg_ref and len(ts_ecg_ref) >= 2: duracion_total_ms = ts_ecg_ref[-1] - ts_ecg_ref[0]
    elif tiene_flujo: duracion_total_ms = ts_flujo[-1] - ts_flujo[0]
    elif tiene_accz: duracion_total_ms = ts_accz[-1] - ts_accz[0]
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
def obtener_datos_sensores(request: Request):
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
        result = []
        for r in rows:
            r = dict(r)
            r["hora_detectada"] = timedelta_a_str(r.get("hora_detectada"))
            result.append(r)
        return result
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
            INSERT INTO interrupciones
                (hora_sesion_id, numero_interrupcion, hora_detectada, duracion_segundos, spo2, frecuencia_cardiaca)
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
# PANEL DE ADMINISTRACIÓN - FRONTEND ORIGINAL + WEBSOCKETS
# ─────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    if not verificar_sesion(request):
        return RedirectResponse(url="/login")

    html = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AOS — Admin Panel</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: Arial, sans-serif; background: #EEF5FB; color: #2C4A5A; }
            header { background: white; padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #D4E8F3; box-shadow: 0 2px 10px rgba(44,74,90,0.05); }
            header h1 { font-family: 'Times New Roman', serif; font-size: 22px; color: #2C4A5A; }
            .btn-logout { text-decoration: none; background: #D65C5C; color: white; padding: 8px 16px; border-radius: 4px; font-size: 13px; font-weight: bold; }
            .btn-logout:hover { background: #B84B4B; }
            
            .nav-bar { display: flex; gap: 10px; padding: 0 40px; background: white; border-bottom: 1px solid #D4E8F3; }
            .nav-btn { background: none; border: none; padding: 16px 20px; font-size: 14px; color: #5A7A8A; cursor: pointer; font-weight: bold; border-bottom: 3px solid transparent; }
            .nav-btn:hover { color: #2C4A5A; }
            .nav-btn.active { color: #7AAFC5; border-bottom-color: #7AAFC5; }
            
            .container { padding: 40px; max-width: 1200px; margin: 0 auto; }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
            
            h2 { margin-bottom: 20px; font-size: 20px; color: #2C4A5A; display: flex; justify-content: space-between; align-items: center; }
            .btn-primary { background: #7AAFC5; color: white; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 13px; }
            .btn-primary:hover { background: #5B9AB5; }
            
            .card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }
            .card { background: white; padding: 20px; border-radius: 8px; border: 1px solid #D4E8F3; box-shadow: 0 4px 12px rgba(44,74,90,0.04); }
            .card h3 { font-size: 16px; margin-bottom: 12px; color: #2C4A5A; border-bottom: 1px solid #EEF5FB; padding-bottom: 8px; }
            .card p { font-size: 13px; color: #5A7A8A; margin-bottom: 6px; line-height: 1.4; }
            .card strong { color: #2C4A5A; }
            .card-actions { margin-top: 15px; display: flex; gap: 8px; }
            .btn-edit { background: #E2E8F0; color: #475569; padding: 6px 12px; border: none; border-radius: 4px; font-size: 12px; cursor: pointer; }
            .btn-del { background: #FEE2E2; color: #DC2626; padding: 6px 12px; border: none; border-radius: 4px; font-size: 12px; cursor: pointer; }

            /* Tarjetas y Canvas de Monitoreo en Vivo */
            .indicadores-vivo { display: flex; justify-content: center; gap: 20px; margin-bottom: 20px; }
            .tarjeta-vivo { background: white; padding: 15px 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center; border: 1px solid #D4E8F3; width: 200px;}
            .tarjeta-vivo h3 { margin: 0; font-size: 14px; color: #5A7A8A; border:none; padding:0;}
            .tarjeta-vivo p { margin: 5px 0 0 0; font-size: 24px; font-weight: bold; color: #2C4A5A; }
            .chart-container { background: white; padding: 15px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #D4E8F3; height: 250px; }
            canvas { width: 100% !important; height: 100% !important; }
            
            table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 12px rgba(44,74,90,0.04); }
            th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #EEF5FB; font-size: 13px; }
            th { background: #F8FAFC; color: #5A7A8A; font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px; }
            td { color: #2C4A5A; }
            
            .modal-overlay { display: none; position: fixed; top:0; left:0; width:100%; height:100%; background: rgba(44,74,90,0.4); justify-content: center; align-items: center; z-index: 1000; }
            .modal { background: white; padding: 30px; border-radius: 8px; width: 400px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); }
            .modal h3 { margin-bottom: 20px; font-size: 18px; }
            .modal label { display: block; font-size: 12px; margin-bottom: 4px; font-weight: bold; color: #5A7A8A; }
            .modal input, .modal select { width: 100%; padding: 8px 12px; margin-bottom: 16px; border: 1px solid #D4E8F3; border-radius: 4px; }
            .modal-btns { display: flex; justify-content: flex-end; gap: 10px; }
            
            .toast { position: fixed; bottom: 20px; right: 20px; background: #4CAF50; color: white; padding: 12px 24px; border-radius: 4px; font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); opacity: 0; transition: opacity 0.3s; z-index: 2000; pointer-events: none; }
        </style>
    </head>
    <body>
        <header>
            <h1>⚙️ AOS — Panel de Administración</h1>
            <a href="/logout" class="btn-logout">Cerrar Sesión</a>
        </header>
        
        <div class="nav-bar">
            <button class="nav-btn active" onclick="mostrarTab('pacientes', this)">👥 Pacientes</button>
            <button class="nav-btn" onclick="mostrarTab('usuarios', this)">🔑 Usuarios</button>
            <button class="nav-btn" onclick="mostrarTab('monitoreo', this)">📊 Historial ESP32</button>
            <button class="nav-btn" onclick="mostrarTab('vivo', this)">🟢 Señales en Vivo</button>
            <button class="nav-btn" onclick="mostrarTab('visor', this)">📈 Visor de Señales</button>
        </div>

        <div class="container">
            <div id="tab-pacientes" class="tab-content active">
                <h2>Directorio de Pacientes <button class="btn-primary" onclick="abrirModalPac()">+ Nuevo Paciente</button></h2>
                <div class="card-grid" id="grid-pacientes"></div>
            </div>

            <div id="tab-usuarios" class="tab-content">
                <h2>Cuentas de Acceso <button class="btn-primary" onclick="abrirModalUsr()">+ Nuevo Usuario</button></h2>
                <table id="tabla-usuarios">
                    <thead><tr><th>ID</th><th>Usuario</th><th>Acciones</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>

            <div id="tab-monitoreo" class="tab-content">
                <h2>Historial de Eventos Guardados</h2>
                <div class="card-grid" id="grid-monitoreo"></div>
            </div>

            <div id="tab-vivo" class="tab-content">
                <h2>🟢 Monitoreo de Paciente en Vivo</h2>
                <div id="ws-status" style="text-align: center; color: #888; font-size: 14px; margin-top: -10px; margin-bottom: 20px;">Buscando conexión con el equipo... ⏳</div>

                <div class="indicadores-vivo">
                    <div class="tarjeta-vivo">
                        <h3>SpO2 (%)</h3>
                        <p id="val-spo2">--</p>
                    </div>
                    <div class="tarjeta-vivo">
                        <h3>Apneas Detectadas</h3>
                        <p id="val-apneas">--</p>
                    </div>
                </div>
                
                <div class="chart-container">
                    <canvas id="chartECG"></canvas>
                </div>
                <div class="chart-container">
                    <canvas id="chartFlujo"></canvas>
                </div>
            </div>

            <div id="tab-visor" class="tab-content">
                <h2>Visor de Señales (Pendiente de integrar visor web)</h2>
                <p>Aquí se cargará la herramienta para analizar a profundidad las señales guardadas.</p>
            </div>
        </div>

        <div id="modal-confirm" class="modal-overlay">
            <div class="modal">
                <h3 id="confirm-title" style="color:#D65C5C;">Atención</h3>
                <p id="confirm-msg" style="font-size:14px; color:#5A7A8A; margin-bottom:20px; line-height:1.5;"></p>
                <div class="modal-btns">
                    <button class="btn-edit" onclick="cerrarConfirm()">Cancelar</button>
                    <button class="btn-del" id="confirm-btn">Sí, eliminar</button>
                </div>
            </div>
        </div>

        <div id="modal-pac" class="modal-overlay">
            <div class="modal">
                <h3 id="modal-pac-title">Nuevo Paciente</h3>
                <input type="hidden" id="pac-id">
                
                <label>Nombre Completo</label>
                <input type="text" id="pac-nombre">
                
                <label>Fecha de Estudio</label>
                <input type="date" id="pac-fecha">
                
                <div style="display:flex; gap:10px;">
                    <div style="flex:1;">
                        <label>Edad</label>
                        <input type="number" id="pac-edad">
                    </div>
                    <div style="flex:1;">
                        <label>Sexo</label>
                        <select id="pac-sexo">
                            <option value="Masculino">Masculino</option>
                            <option value="Femenino">Femenino</option>
                        </select>
                    </div>
                </div>
                
                <div style="display:flex; gap:10px;">
                    <div style="flex:1;">
                        <label>IMC</label>
                        <input type="number" step="0.1" id="pac-imc">
                    </div>
                    <div style="flex:1;">
                        <label>Cardiovascular</label>
                        <select id="pac-cardio">
                            <option value="No">No</option>
                            <option value="Sí">Sí</option>
                        </select>
                    </div>
                </div>

                <label>Puntaje Epworth</label>
                <input type="number" id="pac-epworth">

                <div class="modal-btns">
                    <button class="btn-del" onclick="cerrarModals()">Cancelar</button>
                    <button class="btn-primary" onclick="guardarPaciente()">Guardar</button>
                </div>
            </div>
        </div>

        <div id="modal-usr" class="modal-overlay">
            <div class="modal">
                <h3>Nuevo Usuario</h3>
                <label>Usuario (Login)</label>
                <input type="text" id="usr-nombre">
                <label>Contraseña</label>
                <input type="password" id="usr-pass">
                <div class="modal-btns">
                    <button class="btn-del" onclick="cerrarModals()">Cancelar</button>
                    <button class="btn-primary" onclick="guardarUsuario()">Crear</button>
                </div>
            </div>
        </div>

        <div id="toast" class="toast">Acción completada</div>

        <script>
            function mostrarTab(id, btn) {
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
                document.getElementById('tab-' + id).classList.add('active');
                btn.classList.add('active');
                if(id === 'pacientes') cargarPacientes();
                if(id === 'usuarios') cargarUsuarios();
                if(id === 'monitoreo') cargarSensores();
            }

            function mostrarToast(msg) {
                const t = document.getElementById('toast');
                t.innerText = msg;
                t.style.opacity = '1';
                setTimeout(() => t.style.opacity = '0', 3000);
            }

            // SISTEMA ORIGINAL DE CONFIRMACIÓN DE ELIMINACIÓN
            let accionConfirmar = null;
            function abrirConfirm(titulo, mensaje, accion) {
                document.getElementById('confirm-title').innerText = titulo;
                document.getElementById('confirm-msg').innerText = mensaje;
                accionConfirmar = accion;
                document.getElementById('modal-confirm').style.display = 'flex';
            }
            function cerrarConfirm() {
                document.getElementById('modal-confirm').style.display = 'none';
                accionConfirmar = null;
            }
            document.getElementById('confirm-btn').addEventListener('click', () => {
                if (accionConfirmar) accionConfirmar();
                cerrarConfirm();
            });

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

            async function cargarPacientes() {
                const res = await fetch('/api/pacientes');
                const data = await res.json();
                const grid = document.getElementById('grid-pacientes');
                grid.innerHTML = '';
                data.forEach(p => {
                    grid.innerHTML += `
                        <div class="card">
                            <h3>${p.nombre}</h3>
                            <p><strong>Fecha Estudio:</strong> ${p.fecha_estudio || 'N/A'}</p>
                            <p><strong>Edad:</strong> ${p.edad || '--'} años | <strong>Sexo:</strong> ${p.sexo || '--'}</p>
                            <p><strong>IMC:</strong> ${p.imc || '--'} | <strong>Epworth:</strong> ${p.epworth || '--'}</p>
                            <p><strong>Enf. Cardio:</strong> ${p.enfermedad_cardiovascular || 'No'}</p>
                            <div class="card-actions">
                                <button class="btn-edit" onclick='editarPac(${JSON.stringify(p)})'>Editar</button>
                                <button class="btn-del" onclick="confirmarEliminarPaciente(${p.id}, '${p.nombre}')">Eliminar</button>
                            </div>
                        </div>
                    `;
                });
            }

            async function cargarUsuarios() {
                const res = await fetch('/api/usuarios');
                const data = await res.json();
                const tb = document.querySelector('#tabla-usuarios tbody');
                tb.innerHTML = '';
                data.forEach(u => {
                    tb.innerHTML += `
                        <tr>
                            <td>${u.id}</td>
                            <td><strong>${u.usuario}</strong></td>
                            <td>
                                ${u.usuario !== 'admin' ? `<button class="btn-del" onclick="confirmarEliminarUsuario(${u.id}, '${u.usuario}')">🗑️ Eliminar</button>` : '<span style="font-size:12px;color:#888;">Admin principal</span>'}
                            </td>
                        </tr>
                    `;
                });
            }

            async function cargarSensores() {
                const res = await fetch('/datos-sensores');
                const data = await res.json();
                const grid = document.getElementById('grid-monitoreo');
                grid.innerHTML = '';
                if(data.error) {
                    grid.innerHTML = `<p style="color:red">Error: ${data.error}</p>`;
                    return;
                }
                if(data.length === 0) {
                    grid.innerHTML = `<p style="color:#888; grid-column: 1/-1; text-align:center;">No hay datos de interrupciones registrados en la base de datos.</p>`;
                    return;
                }
                data.forEach(d => {
                    grid.innerHTML += `
                        <div class="card">
                            <h3 style="color:#D65C5C;">🚨 Apnea Detectada #${d.numero_apnea}</h3>
                            <p><strong>Paciente:</strong> ${d.paciente_nombre}</p>
                            <p><strong>Hora Evento:</strong> ${d.hora_detectada || '--'}</p>
                            <p><strong>Duración:</strong> ${d.duracion_apnea} seg</p>
                            <hr style="border:0; border-top:1px solid #EEF5FB; margin:10px 0;">
                            <div style="display:flex; justify-content:space-between; font-size:12px;">
                                <span>🩸 SpO2: <strong>${d.spo2 || '--'}%</strong></span>
                                <span>💓 ECG/FC: <strong>${d.ecg || '--'}</strong></span>
                            </div>
                        </div>
                    `;
                });
            }

            function abrirModalPac() {
                document.getElementById('pac-id').value = '';
                document.getElementById('pac-nombre').value = '';
                document.getElementById('pac-fecha').value = '';
                document.getElementById('pac-edad').value = '';
                document.getElementById('pac-imc').value = '';
                document.getElementById('pac-epworth').value = '';
                document.getElementById('modal-pac-title').innerText = 'Nuevo Paciente';
                document.getElementById('modal-pac').style.display = 'flex';
            }

            function editarPac(p) {
                document.getElementById('pac-id').value = p.id;
                document.getElementById('pac-nombre').value = p.nombre;
                document.getElementById('pac-fecha').value = p.fecha_estudio;
                document.getElementById('pac-edad').value = p.edad;
                document.getElementById('pac-sexo').value = p.sexo;
                document.getElementById('pac-cardio').value = p.enfermedad_cardiovascular;
                document.getElementById('pac-imc').value = p.imc;
                document.getElementById('pac-epworth').value = p.epworth;
                document.getElementById('modal-pac-title').innerText = 'Editar Paciente';
                document.getElementById('modal-pac').style.display = 'flex';
            }

            function abrirModalUsr() {
                document.getElementById('usr-nombre').value = '';
                document.getElementById('usr-pass').value = '';
                document.getElementById('modal-usr').style.display = 'flex';
            }

            function cerrarModals() {
                document.getElementById('modal-pac').style.display = 'none';
                document.getElementById('modal-usr').style.display = 'none';
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
                cerrarModals(); cargarPacientes(); mostrarToast('✅ Guardado');
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

            // ── LÓGICA DE WEBSOCKETS Y GRÁFICAS EN TIEMPO REAL ──
            const maxPuntos = 50; 
            const ctxECG = document.getElementById('chartECG').getContext('2d');
            const ctxFlujo = document.getElementById('chartFlujo').getContext('2d');

            const commonOptions = {
                responsive: true,
                maintainAspectRatio: false,
                animation: false, 
                scales: { x: { display: false }, y: { grid: { color: '#EEF5FB' } } },
                elements: { point: { radius: 0 } } 
            };

            const chartECG = new Chart(ctxECG, {
                type: 'line',
                data: { labels: [], datasets: [{ label: 'ECG / FC', data: [], borderColor: '#D65C5C', borderWidth: 2, tension: 0.2 }] },
                options: commonOptions
            });

            const chartFlujo = new Chart(ctxFlujo, {
                type: 'line',
                data: { labels: [], datasets: [{ label: 'Flujo Respiratorio', data: [], borderColor: '#7AAFC5', borderWidth: 2, tension: 0.2 }] },
                options: commonOptions
            });

            const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            const wsUrl = wsProtocol + window.location.host + '/ws';
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                document.getElementById('ws-status').innerHTML = "✅ Equipo conectado y transmitiendo";
                document.getElementById('ws-status').style.color = "#4CAF50";
            };

            ws.onclose = () => {
                document.getElementById('ws-status').innerHTML = "❌ Conexión perdida. Intentando reconectar...";
                document.getElementById('ws-status').style.color = "#D65C5C";
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    
                    if(data.spo2 !== undefined) document.getElementById('val-spo2').innerText = data.spo2;
                    if(data.no_apnea !== undefined) document.getElementById('val-apneas').innerText = data.no_apnea;

                    const timeNow = new Date().toLocaleTimeString();

                    if(data.ecg && Array.isArray(data.ecg)) {
                        data.ecg.forEach(val => {
                            chartECG.data.labels.push(timeNow);
                            chartECG.data.datasets[0].data.push(val);
                            if(chartECG.data.labels.length > maxPuntos * 5) { 
                                chartECG.data.labels.shift();
                                chartECG.data.datasets[0].data.shift();
                            }
                        });
                        chartECG.update();
                    }

                    if(data.flujo && Array.isArray(data.flujo)) {
                        data.flujo.forEach(val => {
                            chartFlujo.data.labels.push(timeNow);
                            chartFlujo.data.datasets[0].data.push(val);
                            if(chartFlujo.data.labels.length > maxPuntos * 5) {
                                chartFlujo.data.labels.shift();
                                chartFlujo.data.datasets[0].data.shift();
                            }
                        });
                        chartFlujo.update();
                    }
                } catch(e) {
                    console.log("Error procesando JSON:", event.data);
                }
            };

            window.onload = cargarPacientes;
        </script>
    </body>
    </html>
    """
    return html
