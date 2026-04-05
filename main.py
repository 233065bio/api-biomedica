from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import mysql.connector
import os

app = FastAPI()

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
        port=int(os.getenv("MYSQL_PORT") or 3306)
    )

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

        # Usuario por defecto
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

class Interrupcion(BaseModel):
    hora_sesion_id: int
    numero_interrupcion: int
    hora_detectada: str
    duracion_segundos: float
    spo2: float
    frecuencia_cardiaca: float

class LoteInterrupciones(BaseModel):
    interrupciones: List[Interrupcion]

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
async def crear_interrupcion(data: Interrupcion):
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
# ENDPOINTS CONSULTA
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
# DASHBOARD
# ─────────────────────────────────────────────
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
            .banner { background: #EEF5FB; padding: 14px 30px; border-bottom: 1px solid #D4E8F3; }
            .banner h1 { font-family: 'Times New Roman', serif; font-size: 22px; color: #2C4A5A; }
            .content { padding: 20px 30px; }
            .search-bar { background: #EEF5FB; border: 1px solid #D4E8F3; padding: 8px 14px;
                          width: 400px; border-radius: 4px; font-size: 14px; color: #2C4A5A; }
            table { width: 100%; border-collapse: collapse; margin-top: 16px; }
            th { background: #EEF5FB; color: #2C4A5A; padding: 10px; text-align: left;
                 font-size: 13px; border-bottom: 2px solid #D4E8F3; }
            td { padding: 10px; border-bottom: 1px solid #D4E8F3; font-size: 13px; }
            tr:hover { background: #F5FAFD; cursor: pointer; }
            .badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }
            .badge-ok   { background: #EEF8F2; color: #2E7D52; }
            .badge-warn { background: #FFF8EC; color: #B07020; }
            .badge-crit { background: #FFF0EE; color: #A02020; }
            .section-title { font-size: 14px; font-weight: bold; color: #5A7A8A;
                             margin: 20px 0 10px; text-transform: uppercase;
                             letter-spacing: 1px; }
        </style>
    </head>
    <body>
        <div class="banner">
            <h1>📋 AOS — Base de Datos de Pacientes</h1>
        </div>
        <div class="content">
            <input class="search-bar" id="buscador" placeholder="🔍 Buscar paciente..."
                   oninput="filtrar()">
            <div class="section-title">Pacientes registrados</div>
            <table id="tabla">
                <thead>
                    <tr>
                        <th>Nombre</th>
                        <th>Fecha estudio</th>
                        <th>Edad</th>
                        <th>Sexo</th>
                        <th>Enf. cardiovascular</th>
                        <th>IMC</th>
                        <th>EPWORTH</th>
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
                        '<td>' + (p.fecha_estudio || '--') + '</td>' +
                        '<td>' + (p.edad || '--') + '</td>' +
                        '<td>' + (p.sexo || '--') + '</td>' +
                        '<td>' + (p.enfermedad_cardiovascular || '--') + '</td>' +
                        '<td><span class="badge ' + imc + '">' + (p.imc || '--') + '</span></td>' +
                        '<td><span class="badge ' + epw + '">' + (p.epworth || '--') + '</span></td>' +
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
        "/dashboard", "/pacientes", "/senales", "/interrupciones"
    ]}
