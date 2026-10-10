import os
from dotenv import find_dotenv, dotenv_values

ruta = find_dotenv(usecwd=True)
print("Archivo encontrado:", ruta or "NINGUNO")
print("Variables:", list(dotenv_values(ruta).keys()) if ruta else [])
print("Archivos en la carpeta:", [f for f in os.listdir(".") if "env" in f.lower()])