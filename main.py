from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
import mysql.connector
import os

app = FastAPI()

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
        port=int(os.getenv("MYSQL_PORT", 3306))
    )

@app.on_event("startup")
def startup_event():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS datos_sensores (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tiempo_ms BIGINT,
                ecg FLOAT,
                spo2 FLOAT,
                acc_x FLOAT,
                acc_y FLOAT,
                acc_z FLOAT,
                giro_x FLOAT,
                giro_y FLOAT,
                giro_z FLOAT,
                flujo_respiratorio FLOAT,
                fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Tabla verificada/creada con éxito")
    except Exception as e:
        print(f"❌ Error al crear tabla: {e}")

class LecturaSensor(BaseModel):
    tiempo_ms: int
    ecg: float
    spo2: float
    acc_x: float
    acc_y: float
    acc_z: float
    giro_x: float
    giro_y: float
    giro_z: float
    flujo_respiratorio: float

@app.post("/subir-datos")
async def recibir_datos(lecturas: List[LecturaSensor]):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        sql = """INSERT INTO datos_sensores 
                 (tiempo_ms, ecg, spo2, acc_x, acc_y, acc_z, giro_x, giro_y, giro_z, flujo_respiratorio) 
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        valores = [(l.tiempo_ms, l.ecg, l.spo2, l.acc_x, l.acc_y, l.acc_z,
                    l.giro_x, l.giro_y, l.giro_z, l.flujo_respiratorio) for l in lecturas]
        cursor.executemany(sql, valores)
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "mensaje": f"{len(lecturas)} registros guardados"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/datos")
def obtener_datos():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM datos_sensores ORDER BY fecha_registro DESC LIMIT 10")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.get("/datos/todos")
def obtener_todos():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM datos_sensores ORDER BY fecha_registro DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.get("/datos/count")
def contar_datos():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM datos_sensores")
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return {"total_registros": count}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Monitor Biomédico</title>
        <style>
            body { font-family: Arial; padding: 20px; background: #1a1a2e; color: white; }
            h1 { color: #00d4ff; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th { background: #00d4ff; color: black; padding: 10px; }
            td { padding: 8px; border-bottom: 1px solid #333; text-align: center; font-size: 13px; }
            tr:hover { background: #16213e; }
            .btn { background: #00d4ff; color: black; padding: 10px 20px;
                   border: none; cursor: pointer; border-radius: 5px; margin: 5px; font-weight: bold; }
            .btn:hover { background: #00b8d9; }
            #total { color: #00d4ff; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>📊 Monitor de Sensores Biomédicos</h1>
        <button class="btn" onclick="cargarDatos()">🔄 Últimos 10</button>
        <button class="btn" onclick="cargarTodos()">📋 Ver todos</button>
        <p id="total"></p>
        <table>
            <thead>
                <tr>
                    <th>ID</th><th>Tiempo (ms)</th><th>ECG</th><th>SpO2</th>
                    <th>Acc X</th><th>Acc Y</th><th>Acc Z</th>
                    <th>Giro X</th><th>Giro Y</th><th>Giro Z</th>
                    <th>Flujo Resp.</th><th>Fecha</th>
                </tr>
            </thead>
            <tbody id="cuerpo"></tbody>
        </table>
        <script>
            async function cargarDatos() {
                const res = await fetch('/datos');
                const datos = await res.json();
                mostrar(datos);
            }
            async function cargarTodos() {
                const res = await fetch('/datos/todos');
                const datos = await res.json();
                mostrar(datos);
            }
            function mostrar(datos) {
                document.getElementById('total').innerText = 'Total registros mostrados: ' + datos.length;
                const cuerpo = document.getElementById('cuerpo');
                cuerpo.innerHTML = '';
                datos.forEach(d => {
                    cuerpo.innerHTML += '<tr>' +
                        '<td>' + d.id + '</td>' +
                        '<td>' + d.tiempo_ms + '</td>' +
                        '<td>' + d.ecg + '</td>' +
                        '<td>' + d.spo2 + '</td>' +
                        '<td>' + d.acc_x + '</td>' +
                        '<td>' + d.acc_y + '</td>' +
                        '<td>' + d.acc_z + '</td>' +
                        '<td>' + d.giro_x + '</td>' +
                        '<td>' + d.giro_y + '</td>' +
                        '<td>' + d.giro_z + '</td>' +
                        '<td>' + d.flujo_respiratorio + '</td>' +
                        '<td>' + d.fecha_registro + '</td>' +
                        '</tr>';
                });
            }
            cargarDatos();
        </script>
    </body>
    </html>
    """

@app.get("/")
def home():
    return {"mensaje": "API de Monitoreo activa y Tabla vinculada"}
