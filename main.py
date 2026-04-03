from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import mysql.connector
import os

app = FastAPI()

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQLHOST"),
        user=os.getenv("MYSQLUSER"),
        password=os.getenv("MYSQLPASSWORD"),
        database=os.getenv("MYSQLDATABASE"),
        port=int(os.getenv("MYSQLPORT", 3306))
    )

# ESTO CREARÁ LA TABLA POR TI AL INICIAR
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
        print("Tabla verificada/creada con éxito")
    except Exception as e:
        print(f"Error al crear tabla: {e}")

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
        valores = [(l.tiempo_ms, l.ecg, l.spo2, l.acc_x, l.acc_y, l.acc_z, l.giro_x, l.giro_y, l.giro_z, l.flujo_respiratorio) for l in lecturas]
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
    cursor.execute("SELECT * FROM datos_sensores LIMIT 10")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows        

@app.get("/")
def home():
    return {"mensaje": "API de Monitoreo activa y Tabla vinculada"}
