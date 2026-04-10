from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import mysql.connector
import os
import bcrypt
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

# --- MODELOS ---
class AnotacionModel(BaseModel):
    anotacion: str

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

class LoginRequest(BaseModel):
    usuario: str
    contrasena: str

def timedelta_a_str(valor):
    if valor is None: return None
    if hasattr(valor, 'total_seconds'):
        total = int(valor.total_seconds())
        h, m, s = total // 3600, (total % 3600) // 60, total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    return str(valor)

# ─────────────────────────────────────────────
# NUEVO ENDPOINT: ELIMINAR INTERRUPCIÓN (Cambio 5 Backend)
# ─────────────────────────────────────────────
@app.delete("/interrupciones/{id}")
async def eliminar_interrupcion_endpoint(id: int, request: Request):
    if not verificar_sesion(request): raise HTTPException(status_code=401)
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Primero eliminar señales asociadas por la llave foránea
        cursor.execute("DELETE FROM senales_esp32 WHERE interrupcion_id = %s", (id,))
        cursor.execute("DELETE FROM interrupciones WHERE id = %s", (id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# ACTUALIZACIÓN: OBTENER DATOS SENSORES (Cambio 3)
# ─────────────────────────────────────────────
@app.get("/datos-sensores")
def obtener_datos_sensores(request: Request):
    if not verificar_sesion(request): raise HTTPException(status_code=401)
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT
                i.id AS interrupcion_id,
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
        return [dict(r, hora_detectada=timedelta_a_str(r["hora_detectada"])) for r in rows]
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────────────────────
# INTERFAZ ADMIN (Cambios 1, 2, 4, 5 Frontend)
# ─────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not verificar_sesion(request): return RedirectResponse(url="/login")
    
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Panel AOS</title></head>
    <body>
        <table>
            <thead>
                <tr><th>Paciente</th><th>Hora</th><th>SpO2</th><th>ECG</th><th>Acce Z</th><th>Flujo</th><th>N° Apnea</th><th>Duración</th><th>Acciones</th></tr>
            </thead>
            <tbody id="tbody-monitoreo"></tbody>
        </table>

    <script>
        // Cambio 2: Cargar Monitoreo con botón eliminar
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
                    <td>
                        <button class="btn btn-danger" onclick="eliminarInterrupcion(${d.interrupcion_id}, 'monitoreo')">🗑️</button>
                    </td>
                </tr>
            `).join('');
        }

        // Cambio 4: Renderizar lista en visor
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
                <div class="interr-item" id="item-${i.id}">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                        <div onclick="cargarSenales(${i.id}, document.getElementById('item-${i.id}'))" style="flex:1; cursor:pointer;">
                            <div class="interr-title">Apnea #${numApnea} · Hora ${horaOrden}</div>
                            <div class="interr-meta">🕐 ${i.hora_detectada || '--'} &nbsp;|&nbsp; ⏱️ ${i.duracion_segundos}s</div>
                            <div style="margin-top:5px;">
                                <span class="badge ${spo2Class}">SpO₂ ${i.spo2}%</span>
                                ${tieneSenales ? `<span class="interr-badge signal-count">📶 ${i.total_senales} muestras</span>` : `<span class="interr-badge" style="background:#FFF0EE;color:#A02020;">Sin señales</span>`}
                            </div>
                        </div>
                        <button class="btn btn-danger" style="margin-left:8px; flex-shrink:0;" onclick="eliminarInterrupcion(${i.id}, 'senales')">🗑️</button>
                    </div>
                </div>`;
            }).join('');
        }

        // Cambio 5: Función eliminar
        async function eliminarInterrupcion(id, origen) {
            if (!confirm('¿Eliminar esta apnea y todas sus señales?')) return;
            const res = await fetch('/interrupciones/' + id, { method: 'DELETE' });
            if (!res.ok) { alert('❌ Error al eliminar'); return; }

            if(typeof senalesCache !== 'undefined') delete senalesCache[id];
            alert('✅ Apnea eliminada');

            if (origen === 'monitoreo') {
                cargarMonitoreo();
            } else {
                if(typeof resetCharts === 'function') resetCharts();
                const sel = document.getElementById('sel-sesion');
                if (sel && sel.value) onSesionChange();
            }
        }
    </script>
    </body>
    </html>
    """
