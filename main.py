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
# HELPER: convertir timedelta a string HH:MM:SS
# ─────────────────────────────────────────────
def timedelta_a_str(valor):
    if valor is None:
        return None
    if hasattr(valor, 'total_seconds'):
        total = int(valor.total_seconds())
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    return str(valor)

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
            body {{ font-family: Arial, sans-serif; background: #EEF5FB;
                   display: flex; justify-content: center; align-items: center;
                   height: 100vh; }}
            .card {{ background: white; border-radius: 10px; padding: 40px;
                    width: 380px; box-shadow: 0 8px 32px rgba(44,74,90,0.12);
                    border: 1px solid #D4E8F3; }}
            h1 {{ font-family: 'Times New Roman', serif; color: #2C4A5A;
                 text-align: center; margin-bottom: 8px; font-size: 24px; }}
            p {{ text-align: center; color: #5A7A8A; font-size: 13px; margin-bottom: 28px; }}
            label {{ display: block; font-size: 12px; color: #5A7A8A;
                    font-weight: bold; margin-bottom: 4px; }}
            input {{ width: 100%; padding: 10px 14px; border: 1px solid #D4E8F3;
                    border-radius: 4px; font-size: 14px; background: #EEF5FB;
                    color: #2C4A5A; margin-bottom: 16px; }}
            button {{ width: 100%; padding: 11px; background: #7AAFC5; color: white;
                     border: none; border-radius: 4px; font-size: 15px;
                     font-weight: bold; cursor: pointer; }}
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
        print(f"Error en login BD: {e}")
    return RedirectResponse(url="/login?error=1", status_code=302)

@app.post("/api/login")
def api_login_json(data: LoginRequest):
    if data.usuario == ADMIN_USER and data.contrasena == ADMIN_PASS:
        return {"status": "ok", "usuario": {"id": 0, "usuario": ADMIN_USER}}
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, usuario, contrasena FROM usuarios WHERE usuario = %s",
            (data.usuario,)
        )
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
# ENDPOINTS SESIONES
# ─────────────────────────────────────────────
@app.get("/sesion/por-paciente/{paciente_id}")
def sesion_por_paciente_id(paciente_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id FROM sesiones
            WHERE paciente_id = %s
            ORDER BY fecha DESC LIMIT 1
        """, (paciente_id,))
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
            SELECT s.id
            FROM sesiones s
            JOIN pacientes p ON s.paciente_id = p.id
            WHERE p.nombre = %s
            ORDER BY s.fecha DESC LIMIT 1
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
        cursor.execute("""
            SELECT id, fecha FROM sesiones
            WHERE paciente_id = %s
            ORDER BY fecha DESC
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

# ─────────────────────────────────────────────
# ELIMINAR SESIÓN (en cascada: horas → interrupciones → señales)
# ─────────────────────────────────────────────
@app.delete("/sesiones/{sesion_id}")
def eliminar_sesion(sesion_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Obtener todas las horas de esta sesión
        cursor.execute("SELECT id FROM horas_sesion WHERE sesion_id = %s", (sesion_id,))
        horas = [r[0] for r in cursor.fetchall()]

        for hora_id in horas:
            # 2. Obtener interrupciones de cada hora
            cursor.execute("SELECT id FROM interrupciones WHERE hora_sesion_id = %s", (hora_id,))
            interrupciones = [r[0] for r in cursor.fetchall()]

            # 3. Eliminar señales de cada interrupción
            for interr_id in interrupciones:
                cursor.execute("DELETE FROM senales_esp32 WHERE interrupcion_id = %s", (interr_id,))

            # 4. Eliminar interrupciones de esta hora
            cursor.execute("DELETE FROM interrupciones WHERE hora_sesion_id = %s", (hora_id,))

        # 5. Eliminar horas de la sesión
        cursor.execute("DELETE FROM horas_sesion WHERE sesion_id = %s", (sesion_id,))

        # 6. Eliminar la sesión
        cursor.execute("DELETE FROM sesiones WHERE id = %s", (sesion_id,))

        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "eliminado": "sesion", "id": sesion_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ELIMINAR HORA DE SESIÓN (en cascada: interrupciones → señales)
# ─────────────────────────────────────────────
@app.delete("/horas-sesion/{hora_sesion_id}")
def eliminar_hora_sesion(hora_sesion_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Obtener interrupciones de esta hora
        cursor.execute("SELECT id FROM interrupciones WHERE hora_sesion_id = %s", (hora_sesion_id,))
        interrupciones = [r[0] for r in cursor.fetchall()]

        # 2. Eliminar señales de cada interrupción
        for interr_id in interrupciones:
            cursor.execute("DELETE FROM senales_esp32 WHERE interrupcion_id = %s", (interr_id,))

        # 3. Eliminar interrupciones
        cursor.execute("DELETE FROM interrupciones WHERE hora_sesion_id = %s", (hora_sesion_id,))

        # 4. Eliminar la hora
        cursor.execute("DELETE FROM horas_sesion WHERE id = %s", (hora_sesion_id,))

        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "eliminado": "hora_sesion", "id": hora_sesion_id}
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
# ENDPOINTS INTERRUPCIONES
# ─────────────────────────────────────────────
@app.get("/interrupciones/{hora_sesion_id}")
def interrupciones_por_hora(hora_sesion_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT sesion_id FROM horas_sesion WHERE id = %s
        """, (hora_sesion_id,))
        sesion_row = cursor.fetchone()

        cursor.execute("""
            SELECT id, numero_interrupcion, hora_detectada,
                   duracion_segundos, spo2, frecuencia_cardiaca, anotacion
            FROM interrupciones
            WHERE hora_sesion_id = %s
            ORDER BY id
        """, (hora_sesion_id,))
        rows = cursor.fetchall()

        offset = 0
        if sesion_row:
            sesion_id = sesion_row["sesion_id"]
            cursor.execute("""
                SELECT COUNT(i.id) as cnt
                FROM interrupciones i
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
            SELECT i.id, i.numero_interrupcion, i.hora_detectada,
                   i.duracion_segundos, i.spo2, i.frecuencia_cardiaca,
                   i.anotacion, hs.numero_hora,
                   (SELECT COUNT(*) FROM senales_esp32 WHERE interrupcion_id = i.id) AS total_senales
            FROM interrupciones i
            JOIN horas_sesion hs ON i.hora_sesion_id = hs.id
            WHERE hs.sesion_id = %s
            ORDER BY i.id
        """, (sesion_id,))
        rows = cursor.fetchall()

        cursor.execute("""
            SELECT hs.id as hs_id, hs.numero_hora,
                   ROW_NUMBER() OVER (ORDER BY hs.numero_hora) AS hora_orden
            FROM horas_sesion hs
            WHERE hs.sesion_id = %s
        """, (sesion_id,))
        horas_info = cursor.fetchall()
        hora_orden_map = {}
        for h in horas_info:
            hora_orden_map[h["hs_id"]] = h["hora_orden"]

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
# ELIMINAR INTERRUPCIÓN (señales en cascada)
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
# ENDPOINTS SEÑALES
# ─────────────────────────────────────────────
@app.get("/senales/{interrupcion_id}/{tipo}")
def senales_por_interrupcion(interrupcion_id: int, tipo: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT timestamp_ms, valor
            FROM senales_esp32
            WHERE interrupcion_id = %s AND tipo_senal = %s
            ORDER BY timestamp_ms
        """, (interrupcion_id, tipo))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/senales-completas/{interrupcion_id}")
def senales_completas(interrupcion_id: int):
    """Devuelve todas las señales agrupadas por tipo, con limpieza de outliers."""
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
        for tipo, data in raw.items():
            ts = data["timestamps"]
            vs = data["valores"]
            if tipo.lower() == "ecg":
                ts, vs = _limpiar_outliers_ecg(ts, vs)
            resultado[tipo] = {"timestamps": ts, "valores": vs}

        ts_flujo = raw.get("flujo",  {}).get("timestamps", [])
        vs_flujo = raw.get("flujo",  {}).get("valores",    [])
        ts_accz  = raw.get("acce_z", {}).get("timestamps", [])
        vs_accz  = raw.get("acce_z", {}).get("valores",    [])

        ts_resp, vs_resp = _construir_resp_desde_streaming(
            ts_flujo, vs_flujo, ts_accz, vs_accz
        )
        resultado["frecuencia_respiratoria"] = {
            "timestamps": ts_resp,
            "valores":    vs_resp
        }

        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _limpiar_outliers_ecg(timestamps, valores):
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
    lim_inf = mediana - 5 * iqr
    lim_sup = mediana + 5 * iqr
    valores_limpios = [max(lim_inf, min(lim_sup, v)) for v in valores]
    return timestamps, valores_limpios


import math as _math

def _construir_resp_desde_streaming(ts_flujo, vs_flujo, ts_accz, vs_accz):
    def media_movil_gauss(valores, ventana):
        if len(valores) <= ventana:
            return valores[:]
        result = valores[:]
        for _ in range(3):
            nuevo = []
            for i in range(len(result)):
                inicio = max(0, i - ventana // 2)
                fin = min(len(result), i + ventana // 2 + 1)
                nuevo.append(sum(result[inicio:fin]) / (fin - inicio))
            result = nuevo
        return result

    def interpolar_uniforme(ts, vs, n=500):
        if len(ts) < 2:
            return ts[:], vs[:]
        t0, t1 = ts[0], ts[-1]
        if t0 == t1:
            return ts[:], vs[:]
        paso = (t1 - t0) / (n - 1)
        ts_n = [int(t0 + i * paso) for i in range(n)]
        vs_n = []
        j = 0
        for t in ts_n:
            while j < len(ts) - 2 and ts[j + 1] < t:
                j += 1
            t_a = ts[j]
            t_b = ts[min(j + 1, len(ts) - 1)]
            v_a = vs[j]
            v_b = vs[min(j + 1, len(vs) - 1)]
            frac = (t - t_a) / (t_b - t_a) if t_b != t_a else 0.0
            vs_n.append(v_a + frac * (v_b - v_a))
        return ts_n, vs_n

    def normalizar_al_rango(valores, ref_min, ref_max):
        if not valores:
            return valores
        v_min = min(valores)
        v_max = max(valores)
        if v_max == v_min:
            return [(ref_min + ref_max) / 2] * len(valores)
        escala = (ref_max - ref_min) / (v_max - v_min)
        return [ref_min + (v - v_min) * escala for v in valores]

    def interp_en_ts(ts_src, vs_src, ts_dest):
        resultado = []
        j = 0
        for t in ts_dest:
            if t <= ts_src[0]:
                resultado.append(vs_src[0])
                continue
            if t >= ts_src[-1]:
                resultado.append(vs_src[-1])
                continue
            while j < len(ts_src) - 2 and ts_src[j + 1] < t:
                j += 1
            t_a, t_b = ts_src[j], ts_src[min(j + 1, len(ts_src) - 1)]
            v_a, v_b = vs_src[j], vs_src[min(j + 1, len(vs_src) - 1)]
            frac = (t - t_a) / (t_b - t_a) if t_b != t_a else 0.0
            resultado.append(v_a + frac * (v_b - v_a))
        return resultado

    tiene_flujo = len(ts_flujo) > 1
    tiene_accz  = len(ts_accz) > 1

    if tiene_flujo and not tiene_accz:
        ts_u, vs_i = interpolar_uniforme(ts_flujo, vs_flujo, 500)
        t0 = ts_u[0]
        return [t - t0 for t in ts_u], [round(v, 3) for v in vs_i]

    if tiene_accz and not tiene_flujo:
        ts_u, vs_i = interpolar_uniforme(ts_accz, vs_accz, 500)
        t0 = ts_u[0]
        return [t - t0 for t in ts_u], [round(v, 3) for v in vs_i]

    if tiene_flujo and tiene_accz:
        t_global_min = min(ts_flujo[0], ts_accz[0])
        t_global_max = max(ts_flujo[-1], ts_accz[-1])
        duracion = t_global_max - t_global_min
        if duracion <= 0:
            duracion = 20000

        N = 500
        paso = duracion / (N - 1)
        ts_u = [int(t_global_min + i * paso) for i in range(N)]

        vs_flujo_i = interp_en_ts(ts_flujo, vs_flujo, ts_u)
        vs_accz_i  = interp_en_ts(ts_accz,  vs_accz,  ts_u)

        f_min = min(vs_flujo_i)
        f_max = max(vs_flujo_i)

        vs_accz_norm = normalizar_al_rango(vs_accz_i, f_min, f_max)
        vs_comb = [0.70 * f + 0.30 * a for f, a in zip(vs_flujo_i, vs_accz_norm)]

        t0 = ts_u[0]
        return [t - t0 for t in ts_u], [round(v, 3) for v in vs_comb]

    duracion_ms = 20000
    N = 500
    ts_out = [int(i * duracion_ms / (N - 1)) for i in range(N)]
    vs_out = [round(155 + 30 * _math.sin(2 * _math.pi * 0.25 * t / 1000.0), 3) for t in ts_out]
    return ts_out, vs_out


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
    result = []
    for r in rows:
        r = dict(r)
        if r.get("fecha_estudio"):
            r["fecha_estudio"] = str(r["fecha_estudio"])
        result.append(r)
    return result

@app.post("/pacientes")
def crear_paciente(data: PacienteModel, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO pacientes (nombre, fecha_estudio, edad, sexo, enfermedad_cardiovascular, imc, epworth) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (data.nombre, data.fecha_estudio, data.edad, data.sexo, data.enfermedad_cardiovascular, data.imc, data.epworth)
    )
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
    cursor.execute(
        "UPDATE pacientes SET nombre=%s, fecha_estudio=%s, edad=%s, sexo=%s, enfermedad_cardiovascular=%s, imc=%s, epworth=%s WHERE id=%s",
        (data.nombre, data.fecha_estudio, data.edad, data.sexo, data.enfermedad_cardiovascular, data.imc, data.epworth, paciente_id)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

# ─────────────────────────────────────────────
# ELIMINAR PACIENTE (en cascada: sesiones → horas → interrupciones → señales)
# ─────────────────────────────────────────────
@app.delete("/pacientes/{paciente_id}")
def eliminar_paciente(paciente_id: int, request: Request):
    if not verificar_sesion(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Obtener todas las sesiones del paciente
        cursor.execute("SELECT id FROM sesiones WHERE paciente_id = %s", (paciente_id,))
        sesiones = [r[0] for r in cursor.fetchall()]

        for sesion_id in sesiones:
            # 2. Obtener horas de cada sesión
            cursor.execute("SELECT id FROM horas_sesion WHERE sesion_id = %s", (sesion_id,))
            horas = [r[0] for r in cursor.fetchall()]

            for hora_id in horas:
                # 3. Obtener interrupciones de cada hora
                cursor.execute("SELECT id FROM interrupciones WHERE hora_sesion_id = %s", (hora_id,))
                interrupciones = [r[0] for r in cursor.fetchall()]

                # 4. Eliminar señales de cada interrupción
                for interr_id in interrupciones:
                    cursor.execute("DELETE FROM senales_esp32 WHERE interrupcion_id = %s", (interr_id,))

                # 5. Eliminar interrupciones
                cursor.execute("DELETE FROM interrupciones WHERE hora_sesion_id = %s", (hora_id,))

            # 6. Eliminar horas
            cursor.execute("DELETE FROM horas_sesion WHERE sesion_id = %s", (sesion_id,))

        # 7. Eliminar sesiones
        cursor.execute("DELETE FROM sesiones WHERE paciente_id = %s", (paciente_id,))

        # 8. Eliminar el paciente
        cursor.execute("DELETE FROM pacientes WHERE id = %s", (paciente_id,))

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
    hashed = hash_password(data.contrasena)
    cursor.execute(
        "INSERT INTO usuarios (usuario, contrasena) VALUES (%s, %s)",
        (data.usuario, hashed)
    )
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

async def guardar_usuario():
    pass  # handled above

# ─────────────────────────────────────────────
# ENDPOINT ESP32 — RECEPCIÓN UNIFICADA
# ─────────────────────────────────────────────
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
            cursor.execute(
                "INSERT INTO pacientes (nombre, fecha_estudio) VALUES (%s, %s)",
                (datos.paciente, date.today().isoformat())
            )
            conn.commit()
            paciente_id = cursor.lastrowid

        cursor.execute(
            "SELECT id FROM sesiones WHERE paciente_id = %s AND DATE(fecha) = CURDATE() LIMIT 1",
            (paciente_id,)
        )
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

        cursor.execute(
            "SELECT id FROM horas_sesion WHERE sesion_id = %s AND numero_hora = %s LIMIT 1",
            (sesion_id, hora_num)
        )
        fila = cursor.fetchone()
        if fila:
            hora_sesion_id = fila[0]
        else:
            cursor.execute(
                "INSERT INTO horas_sesion (sesion_id, numero_hora, hora_inicio, hora_fin) VALUES (%s, %s, %s, %s)",
                (sesion_id, hora_num, hora_ini, hora_fin)
            )
            conn.commit()
            hora_sesion_id = cursor.lastrowid

        cursor.execute("""
            SELECT COUNT(i.id) as total
            FROM interrupciones i
            JOIN horas_sesion hs ON i.hora_sesion_id = hs.id
            WHERE hs.sesion_id = %s
        """, (sesion_id,))
        conteo = cursor.fetchone()
        numero_consecutivo = (conteo[0] if conteo else 0) + 1

        cursor.execute("""
            INSERT INTO interrupciones
                (hora_sesion_id, numero_interrupcion, hora_detectada, duracion_segundos, spo2, frecuencia_cardiaca)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (hora_sesion_id, numero_consecutivo, datos.hora, datos.duracion, datos.spo2, datos.ecg))
        conn.commit()
        interrupcion_id = cursor.lastrowid

        timestamp_ms = int(hora_num * 3600000)
        cursor.executemany(
            "INSERT INTO senales_esp32 (interrupcion_id, tipo_senal, timestamp_ms, valor) VALUES (%s, %s, %s, %s)",
            [
                (interrupcion_id, "acce_z", timestamp_ms, datos.acce_z),
                (interrupcion_id, "flujo",  timestamp_ms, datos.flujo),
            ]
        )
        conn.commit()
        cursor.close()
        conn.close()

        return {
            "status": "success",
            "paciente_id":         paciente_id,
            "sesion_id":           sesion_id,
            "hora_sesion_id":      hora_sesion_id,
            "interrupcion_id":     interrupcion_id,
            "numero_consecutivo":  numero_consecutivo
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
        <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
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
            .toast { position: fixed; bottom: 30px; right: 30px; background: #2C4A5A; color: white; padding: 12px 24px; border-radius: 6px; display: none; z-index: 999; }
            .toast.show { display: block; }
            .visor-layout { display: grid; grid-template-columns: 300px 1fr; gap: 20px; }
            .visor-panel { background: #EEF5FB; border-radius: 8px; border: 1px solid #D4E8F3; padding: 16px; }
            .visor-panel h3 { font-size: 13px; color: #5A7A8A; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
            .visor-select { width: 100%; padding: 8px 10px; border: 1px solid #D4E8F3; border-radius: 4px; font-size: 13px; background: white; color: #2C4A5A; margin-bottom: 10px; }
            .interr-list { max-height: 400px; overflow-y: auto; }
            .interr-item { padding: 10px 12px; border-radius: 6px; cursor: pointer; margin-bottom: 6px; background: white; border: 1px solid #D4E8F3; transition: all 0.15s; }
            .interr-item:hover { border-color: #7AAFC5; background: #F0F8FF; }
            .interr-item.selected { border-color: #7AAFC5; background: #E3F2FA; }
            .interr-item .interr-title { font-weight: bold; font-size: 13px; color: #2C4A5A; }
            .interr-item .interr-meta { font-size: 11px; color: #5A7A8A; margin-top: 3px; }
            .interr-item .interr-badge { font-size: 10px; padding: 2px 7px; border-radius: 10px; display: inline-block; margin-top: 4px; }
            .charts-area { display: flex; flex-direction: column; gap: 16px; }
            .chart-card { background: white; border: 1px solid #D4E8F3; border-radius: 8px; padding: 16px; }
            .chart-card h4 { font-size: 12px; color: #5A7A8A; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }
            .chart-card canvas { width: 100% !important; }
            .no-signal { text-align: center; padding: 60px 20px; color: #5A7A8A; font-size: 13px; }
            .signal-count { font-size: 11px; background: #D4E8F3; color: #2C4A5A; padding: 2px 8px; border-radius: 10px; }
            .visor-info { background: white; border: 1px solid #D4E8F3; border-radius: 6px; padding: 12px; margin-bottom: 14px; font-size: 12px; color: #5A7A8A; display: flex; justify-content: space-between; align-items: center; }
            .visor-info strong { color: #2C4A5A; }
            .tabs-signal { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
            .tab-signal { padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: bold; cursor: pointer; border: 2px solid transparent; background: #EEF5FB; color: #5A7A8A; }
            .tab-signal.active { color: white; }
            .tab-signal[data-tipo="frecuencia_respiratoria"].active { background: #4A9E6B; border-color: #4A9E6B; }
            .tab-signal[data-tipo="ecg"].active    { background: #E05C5C; border-color: #E05C5C; }
            .tab-signal[data-tipo="spo2"].active   { background: #5C9AE0; border-color: #5C9AE0; }
            .tab-signal[data-tipo="acce_z"].active { background: #5CBE80; border-color: #5CBE80; }
            .tab-signal[data-tipo="flujo"].active  { background: #E0A55C; border-color: #E0A55C; }
            .loading-msg { text-align:center; color:#7AAFC5; padding:40px; font-size:13px; }
            .fc-badge { background: #EEF8F2; color: #2E7D52; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: bold; }
            /* Modal de confirmación de eliminación */
            .modal-confirm { background: white; border-radius: 8px; padding: 28px; width: 400px; text-align: center; }
            .modal-confirm h3 { color: #2C4A5A; margin-bottom: 10px; }
            .modal-confirm p { color: #5A7A8A; font-size: 13px; margin-bottom: 20px; line-height: 1.5; }
            .modal-confirm .btn-row { display: flex; gap: 10px; justify-content: center; }
            /* Selector de sesión en visor con botón borrar */
            .sel-row { display: flex; gap: 6px; align-items: center; margin-bottom: 10px; }
            .sel-row select { flex: 1; margin-bottom: 0; }
            .btn-del-sm { background: #D65C5C; color: white; border: none; border-radius: 4px; padding: 7px 10px; cursor: pointer; font-size: 13px; flex-shrink: 0; }
            .btn-del-sm:disabled { background: #ccc; cursor: not-allowed; }
        </style>
    </head>
    <body>
        <div class="banner">
            <h1>⚙️ AOS — Panel de Administración</h1>
            <a href="/logout" style="color:#7AAFC5; text-decoration:none; font-size:13px;">Cerrar sesión</a>
        </div>
        <div class="tabs">
            <div class="tab active" onclick="cambiarTab('pacientes', event)">👥 Pacientes</div>
            <div class="tab" onclick="cambiarTab('usuarios', event)">🔑 Usuarios</div>
            <div class="tab" onclick="cambiarTab('monitoreo', event)">📊 Monitoreo ESP32</div>
            <div class="tab" onclick="cambiarTab('senales', event)">📈 Visor de Señales</div>
        </div>
        <div class="content">
            <!-- ══ PACIENTES ══ -->
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

            <!-- ══ USUARIOS ══ -->
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

            <!-- ══ MONITOREO ══ -->
            <div id="sec-monitoreo" class="section">
                <div class="toolbar">
                    <span style="font-size:13px; color:#5A7A8A;">Registros históricos enviados por ESP32</span>
                    <button class="btn btn-primary" onclick="cargarMonitoreo()">🔄 Actualizar</button>
                </div>
                <table>
                    <thead>
                        <tr><th>Paciente</th><th>Hora</th><th>SpO2</th><th>ECG</th><th>Acce Z</th><th>Flujo</th><th>N° Apnea</th><th>Duración</th></tr>
                    </thead>
                    <tbody id="tbody-monitoreo"></tbody>
                </table>
            </div>

            <!-- ══ VISOR DE SEÑALES ══ -->
            <div id="sec-senales" class="section">
                <div class="visor-layout">
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
        </div>

        <!-- MODAL PACIENTE -->
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

        <!-- MODAL USUARIO -->
        <div class="modal-bg" id="modal-usuario">
            <div class="modal">
                <h2>Nuevo Usuario</h2>
                <div class="form-group"><label>Usuario</label><input id="usr-nombre"></div>
                <div class="form-group"><label>Contraseña</label><input id="usr-pass" type="password"></div>
                <div style="text-align:right;">
                    <button class="btn" onclick="cerrarModals()">Cancelar</button>
                    <button class="btn btn-primary" onclick="guardarUsuario()">Guardar</button>
                </div>
            </div>
        </div>

        <!-- MODAL CONFIRMACIÓN ELIMINACIÓN -->
        <div class="modal-bg" id="modal-confirm">
            <div class="modal-confirm">
                <h3 id="confirm-titulo">¿Eliminar?</h3>
                <p id="confirm-texto"></p>
                <div class="btn-row">
                    <button class="btn" style="background:#EEE; color:#333;" onclick="cerrarModals()">Cancelar</button>
                    <button class="btn btn-danger" id="confirm-btn-ok" onclick="">Sí, eliminar</button>
                </div>
            </div>
        </div>

        <div class="toast" id="toast"></div>

        <script>
            let pacientes = [];
            let chartInstances = {};
            let senalesCache = {};
            let apneaActivaId = null;   // id de la interrupción seleccionada
            let sesionActivaId = null;  // id de la sesión seleccionada en el visor

            const SIGNAL_CONFIG = {
                ecg:    { label: 'ECG',                 color: '#E05C5C', bg: 'rgba(224,92,92,0.08)',   unit: 'mV',   emoji: '❤️'  },
                spo2:   { label: 'SpO₂',                color: '#5C9AE0', bg: 'rgba(92,154,224,0.1)',   unit: '%',    emoji: '🩸'  },
                acce_z: { label: 'Aceleración Z',       color: '#5CBE80', bg: 'rgba(92,190,128,0.1)',   unit: 'm/s²', emoji: '🔵'  },
                flujo:  { label: 'Flujo resp.',         color: '#E0A55C', bg: 'rgba(224,165,92,0.1)',   unit: 'ADC',  emoji: '💨'  },
                frecuencia_respiratoria: {
                    label: 'Flujo Respiratorio', color: '#4A9E6B', bg: 'rgba(74,158,107,0.12)', unit: 'ADC', emoji: '🌬️'
                },
            };

            function mostrarToast(msg) {
                const t = document.getElementById('toast');
                t.innerText = msg; t.classList.add('show');
                setTimeout(() => t.classList.remove('show'), 2500);
            }

            function cambiarTab(tab, event) {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
                event.currentTarget.classList.add('active');
                document.getElementById('sec-' + tab).classList.add('active');
                if (tab === 'pacientes') cargarPacientes();
                if (tab === 'usuarios')  cargarUsuarios();
                if (tab === 'monitoreo') cargarMonitoreo();
                if (tab === 'senales')   iniciarVisor();
            }

            // ══════════════════════════════════════════════
            // MODAL DE CONFIRMACIÓN GENÉRICO
            // ══════════════════════════════════════════════
            function abrirConfirm(titulo, texto, fnConfirmar) {
                document.getElementById('confirm-titulo').textContent = titulo;
                document.getElementById('confirm-texto').textContent  = texto;
                const btn = document.getElementById('confirm-btn-ok');
                btn.onclick = () => { cerrarModals(); fnConfirmar(); };
                document.getElementById('modal-confirm').classList.add('show');
            }

            // ══════════════════════════════════════════════
            // VISOR DE SEÑALES
            // ══════════════════════════════════════════════
            async function iniciarVisor() {
                const res = await fetch('/pacientes');
                const pacs = await res.json();
                const sel = document.getElementById('sel-paciente');
                sel.innerHTML = '<option value="">— Seleccionar —</option>';
                pacs.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.id;
                    opt.textContent = p.nombre;
                    sel.appendChild(opt);
                });
            }

            async function onPacienteChange() {
                const pacId = document.getElementById('sel-paciente').value;
                const selSes = document.getElementById('sel-sesion');
                const btnDelSes = document.getElementById('btn-del-sesion');
                selSes.innerHTML = '<option value="">— Seleccionar —</option>';
                selSes.disabled = true;
                btnDelSes.disabled = true;
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
                const btnDelSes = document.getElementById('btn-del-sesion');
                sesionActivaId = sesId ? parseInt(sesId) : null;
                btnDelSes.disabled = !sesId;
                resetCharts();
                if (!sesId) {
                    document.getElementById('interr-list').innerHTML =
                        '<p style="font-size:12px;color:#5A7A8A;text-align:center;padding:20px 0;">Selecciona una sesión</p>';
                    return;
                }
                document.getElementById('interr-list').innerHTML =
                    '<div class="loading-msg">Cargando apneas...</div>';

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
                        <div class="interr-meta">
                            🕐 ${i.hora_detectada || '--'} &nbsp;|&nbsp; ⏱️ ${i.duracion_segundos}s
                        </div>
                        <div style="margin-top:5px;">
                            <span class="badge ${spo2Class}">SpO₂ ${i.spo2}%</span>
                            &nbsp;
                            ${tieneSenales
                                ? `<span class="interr-badge signal-count">📶 ${i.total_senales} muestras</span>`
                                : `<span class="interr-badge" style="background:#FFF0EE;color:#A02020;">Sin señales</span>`}
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
                            `<div class="no-signal" style="padding:40px;">❌ Error al cargar señales: ${err.message}<br><br>
                            <button class="btn btn-primary" onclick="cargarSenales(${interrupcionId}, document.querySelector('.interr-item.selected'))">🔄 Reintentar</button>
                            </div>`;
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

            // ── Eliminar apnea (interrupción) ──────────────────────────────────────
            function confirmarEliminarApnea() {
                if (!apneaActivaId) return;
                abrirConfirm(
                    '🗑️ Eliminar apnea',
                    'Se eliminarán la apnea y todas sus señales almacenadas. Esta acción no se puede deshacer.',
                    async () => {
                        const res = await fetch('/interrupciones/' + apneaActivaId, { method: 'DELETE' });
                        if (res.ok) {
                            mostrarToast('✅ Apnea eliminada');
                            apneaActivaId = null;
                            delete senalesCache[apneaActivaId];
                            resetCharts();
                            // Recargar lista de apneas
                            const sesId = document.getElementById('sel-sesion').value;
                            if (sesId) onSesionChange();
                        } else {
                            mostrarToast('❌ Error al eliminar apnea');
                        }
                    }
                );
            }

            // ── Eliminar sesión completa ───────────────────────────────────────────
            function confirmarEliminarSesion() {
                if (!sesionActivaId) return;
                const sesText = document.getElementById('sel-sesion').options[document.getElementById('sel-sesion').selectedIndex].text;
                abrirConfirm(
                    '🗑️ Eliminar sesión',
                    `Se eliminarán "${sesText}" y todos sus datos: horas, apneas y señales. Esta acción no se puede deshacer.`,
                    async () => {
                        const res = await fetch('/sesiones/' + sesionActivaId, { method: 'DELETE' });
                        if (res.ok) {
                            mostrarToast('✅ Sesión eliminada');
                            sesionActivaId = null;
                            apneaActivaId  = null;
                            senalesCache   = {};
                            resetCharts();
                            // Refrescar selector de sesiones
                            const pacId = document.getElementById('sel-paciente').value;
                            if (pacId) onPacienteChange();
                        } else {
                            mostrarToast('❌ Error al eliminar sesión');
                        }
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
            
                // Frecuencia de muestreo estimada
                const fs = timestamps.length / duracionTotal;
            
                // 1. Diferenciación (resalta pendientes de pico R)
                const diff = [];
                for (let i = 1; i < valores.length; i++) {
                    diff.push(valores[i] - valores[i - 1]);
                }
            
                // 2. Elevar al cuadrado (amplifica y positiviza)
                const sq = diff.map(v => v * v);
            
                // 3. Ventana de integración (150ms aprox)
                const winSamples = Math.max(3, Math.round(fs * 0.15));
                const integrated = [];
                for (let i = 0; i < sq.length; i++) {
                    let sum = 0, count = 0;
                    for (let j = Math.max(0, i - winSamples); j <= i; j++) {
                        sum += sq[j]; count++;
                    }
                    integrated.push(sum / count);
                }
            
                // 4. Umbral adaptativo basado en la media
                const media = integrated.reduce((a, b) => a + b, 0) / integrated.length;
                const umbral = media * 0.5;
            
                // 5. Detectar picos con periodo refractario (200ms mínimo entre picos)
                const refractario = Math.round(fs * 0.2);
                let picos = 0;
                let ultimoPico = -refractario;
                let enPico = false;
            
                for (let i = 1; i < integrated.length - 1; i++) {
                    if (integrated[i] > umbral) {
                        if (!enPico && (i - ultimoPico) >= refractario &&
                            integrated[i] >= integrated[i - 1] && integrated[i] >= integrated[i + 1]) {
                            picos++;
                            ultimoPico = i;
                            enPico = true;
                        }
                    } else {
                        enPico = false;
                    }
                }
            
                if (picos < 2) return null;
                const fc = Math.round((picos / duracionTotal) * 60);
                return (fc >= 30 && fc <= 220) ? fc : null;
            }
            function detectarYFiltrarRuido(timestamps, valores) {
                if (timestamps.length < 50) return { valores, filtrado: false };
            
                const duracion = (timestamps[timestamps.length - 1] - timestamps[0]) / 1000.0;
                const fs = timestamps.length / duracion;
            
                // Detección de energía en banda 55-65 Hz mediante correlación con sinusoide 60Hz
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
            
                // Si la energía en 60 Hz es mayor al 20% de la energía total → hay ruido
                if (energia60 < 0.20) return { valores, filtrado: false };
            
                // Filtro notch IIR para 60 Hz
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
                const cfg  = SIGNAL_CONFIG[tipo] || {
                    label: tipo, color: '#7AAFC5', bg: 'rgba(122,175,197,0.1)', unit: '', emoji: '📶'
                };
                const señal = data[tipo];
                // --- INICIO MODIFICACIÓN ---
                let valoresGrafica = señal.valores;
                let filtroAplicado = false;
                
                if (tipo === 'ecg' && señal.timestamps.length > 0) {
                    const resultado = detectarYFiltrarRuido(señal.timestamps, señal.valores);
                    valoresGrafica = resultado.valores;
                    filtroAplicado = resultado.filtrado;
                }
                // --- FIN MODIFICACIÓN ---

                if (!señal || !señal.timestamps.length) {
                    area.innerHTML = `<div class="no-signal">Sin datos para ${cfg.label}</div>`;
                    return;
                }

                if (chartInstances[tipo]) {
                    chartInstances[tipo].destroy();
                    delete chartInstances[tipo];
                }

                const canvasId = 'chart-' + tipo;
                const vMin = Math.min(...señal.valores);
                const vMax = Math.max(...señal.valores);

                let extraHtml = '';
                }
                if (tipo === 'frecuencia_respiratoria') {
                    const durSeg = señal.timestamps.length > 1
                        ? ((señal.timestamps[señal.timestamps.length-1] - señal.timestamps[0]) / 1000).toFixed(1)
                        : '--';
                    extraHtml = `<div style="margin-bottom:8px;font-size:11px;color:#5A7A8A;">
                        Señal respiratoria combinada (flujo + aceleración Z, suavizado gaussiano) &nbsp;·&nbsp;
                        Duración: ${durSeg}s &nbsp;·&nbsp; Rango: ${vMin.toFixed(0)} – ${vMax.toFixed(0)} ADC
                    </div>`;
                }
                if (tipo === 'ecg') {
                    const fc = calcularFC(señal.timestamps, valoresGrafica); // ← usa valoresGrafica
                    let fcHtml = fc ? `<span class="fc-badge">❤️ ${fc} lpm</span>` : '';
                    let filtroHtml = filtroAplicado
                        ? `<span style="font-size:11px;background:#FFF8EC;color:#B07020;padding:2px 8px;border-radius:10px;margin-left:8px;">⚡ Filtro 60Hz activo</span>`
                        : '';
                    extraHtml = `<div style="margin-bottom:10px;">
                        <span style="font-size:12px;color:#5A7A8A;">Frecuencia cardiaca estimada: </span>
                        ${fcHtml}${filtroHtml}
                    </div>`;
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
                    </div>
                    ${statsHtml}`;

                const ctx = document.getElementById(canvasId).getContext('2d');
                const t0 = señal.timestamps[0];
                const labels = señal.timestamps.map(t => ((t - t0) / 1000).toFixed(2) + 's');

                const esResp  = tipo === 'frecuencia_respiratoria';
                const esEcg   = tipo === 'ecg';
                const esAccz  = tipo === 'acce_z';
                const esFlujo = tipo === 'flujo';
                const tension = esEcg ? 0 : (esResp ? 0.5 : (esAccz ? 0.45 : (esFlujo ? 0.4 : 0.3)));

                let yScaleOpts;
                if (tipo === 'ecg') {
                    const pad = Math.max((vMax - vMin) * 0.1, 5);
                    yScaleOpts = { min: vMin - pad, max: vMax + pad,
                        ticks: { font: { size: 10 }, color: '#5A7A8A' }, grid: { color: '#EEF5FB' } };
                } else {
                    const rango = vMax - vMin;
                    const pad = Math.max(rango * 0.10, 1.0);
                    yScaleOpts = { min: vMin - pad, max: vMax + pad,
                        ticks: { font: { size: 10 }, color: '#5A7A8A' }, grid: { color: '#EEF5FB' } };
                }

                const ocultarPuntos = señal.timestamps.length > 100 || esResp || esAccz || esFlujo;

                chartInstances[tipo] = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: cfg.label,
                            data: señal.valores,
                            borderColor: cfg.color,
                            backgroundColor: cfg.bg,
                            borderWidth: esEcg ? 1.2 : (esResp ? 2.5 : (esAccz ? 1.8 : 1.6)),
                            pointRadius: ocultarPuntos ? 0 : 2,
                            pointHoverRadius: 4,
                            fill: !esEcg,
                            tension: tension,
                            cubicInterpolationMode: 'monotone',
                        }]
                    },
                    options: {
                        responsive: true,
                        animation: { duration: 300 },
                        plugins: {
                            legend: { display: false },
                            tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(2)} ${cfg.unit}` } }
                        },
                        scales: {
                            x: {
                                ticks: { maxTicksLimit: 12, font: { size: 10 }, color: '#5A7A8A' },
                                grid: { color: '#EEF5FB' },
                                title: { display: true, text: 'Tiempo (s)', font: { size: 10 }, color: '#5A7A8A' }
                            },
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

            // ══════════════════════════════════════════════
            // PACIENTES
            // ══════════════════════════════════════════════
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
                            <button class="btn btn-danger" onclick="confirmarEliminarPaciente(${p.id}, '${p.nombre.replace(/'/g, "\\'")}')">🗑️</button>
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

            async function cargarUsuarios() {
                const res = await fetch('/usuarios');
                const data = await res.json();
                document.getElementById('tbody-usuarios').innerHTML = data.map(u => `
                    <tr>
                        <td>${u.id}</td>
                        <td>${u.usuario}</td>
                        <td>
                            <button class="btn btn-danger" onclick="confirmarEliminarUsuario(${u.id}, '${u.usuario}')">🗑️</button>
                        </td>
                    </tr>
                `).join('');
            }

            // ── Confirmaciones de eliminación ──────────────────────────────────────
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
                    `¿Eliminar al usuario "${nombre}"?`,
                    async () => {
                        const res = await fetch('/usuarios/' + id, { method: 'DELETE' });
                        if (res.ok) { mostrarToast('✅ Usuario eliminado'); cargarUsuarios(); }
                        else mostrarToast('❌ Error al eliminar usuario');
                    }
                );
            }

            function abrirModalPaciente() {
                document.getElementById('pac-id').value = '';
                ['pac-nombre','pac-fecha','pac-edad','pac-imc','pac-epworth'].forEach(id => document.getElementById(id).value = '');
                document.getElementById('modal-paciente').classList.add('show');
            }
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

            window.onload = cargarPacientes;
        </script>
    </body>
    </html>
    """
