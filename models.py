from pydantic import BaseModel
from typing import Optional


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
    tipo: Optional[str] = "pruebas"   # "pruebas" | "voluntarios"


class LoginRequest(BaseModel):
    usuario: str
    contrasena: str


class AnotacionModel(BaseModel):
    anotacion: str
