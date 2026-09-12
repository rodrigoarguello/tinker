"""Correr un job a mano:

    sudo docker compose exec web python -m app.jobs music:discover:py

Sirve para probar una fuente sin esperar seis horas, y para el dia que haga
falta forzar un refill antes de una fiesta.
"""
import asyncio
import logging
import sys

from app.jobs.planificador import catalogo

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")


def main() -> int:
    disponibles = {job.nombre: job for job in catalogo()}
    if len(sys.argv) < 2 or sys.argv[1] not in disponibles:
        print("jobs disponibles:")
        for nombre in disponibles:
            print(f"   {nombre}")
        return 1
    job = disponibles[sys.argv[1]]
    resultado = job.correr()
    if asyncio.iscoroutine(resultado):
        resultado = asyncio.run(resultado)
    print(f"{job.nombre}: {resultado}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
