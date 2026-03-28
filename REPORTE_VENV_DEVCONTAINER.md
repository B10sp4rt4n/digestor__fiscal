# Reporte de uso de venv en flujo Devcontainer

Fecha: 2026-03-28
Repositorio: digestor__fiscal

## Contexto
Este proyecto se trabaja hoy dentro de un devcontainer (entorno efimero y aislado del host).
En ese contexto, usar venv es opcional para desarrollo diario, pero sigue siendo util para:

- congelar dependencias reproducibles (lock transitive)
- validar instalacion limpia fuera de la imagen base
- preparar salida empaquetada del proyecto

## Recomendacion por entorno

| Escenario fisico | Usar venv | Recomendacion operativa |
|---|---|---|
| Devcontainer efimero dedicado al repo | Opcional | Instalar global en contenedor para iteracion rapida; regenerar contenedor cuando cambie base |
| Laptop/PC fuera de contenedor | Si | Un .venv por repo para aislamiento |
| CI (jobs aislados) | Opcional | Instalar directo en runner o venv temporal del job |
| Produccion con Docker | No en runtime | Dependencias horneadas en imagen |
| Produccion sin Docker (VM) | Si | Un venv por servicio/version |

## Accion ejecutada ahora
Se creo un venv local del repo y se congelaron dependencias exactas.

Comandos ejecutados:

```bash
python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip freeze > requirements.venv.lock.txt
```

## Evidencia

- venv: .venv
- Python del venv: 3.12.1
- pip del venv: 26.0.1
- lockfile generado: requirements.venv.lock.txt
- total de lineas en lockfile: 71

## Uso recomendado desde este punto

- Para pruebas reproducibles en este contenedor: activar .venv y correr app/tests.
- Para flujo rapido diario en devcontainer: puedes seguir sin activar venv, pero conserva el lockfile para trazabilidad.
- Para salida empaquetada: reconstruir dependencias desde requirements.txt y validar contra requirements.venv.lock.txt.
