# sim_linea_prod

Simulador/juego base de línea de producción para cursos de producción y manufactura (Ingeniería Industrial), implementado con **pygame** y **simpy**.

## Funcionalidades implementadas

- Simulación de órdenes de producción con **eventos reproducibles por semilla**.
- Modelo de línea con reprocesos y control de inventario.
- Importación de órdenes desde **CSV o Excel** (`order_id`, `quantity`).
- Exportación de resultados a **CSV y Excel**.
- Persistencia de sesiones, eventos y avance de estudiantes en **SQLite**.
- Modo red local **profesor/estudiante** por TCP en LAN:
  - Profesor configura semilla/parámetros y escucha avances.
  - Estudiantes solicitan configuración y reportan progreso.
- Visualización en pygame (o modo `--headless` para ejecución sin ventana).

> Nota: no se usan imágenes externas, por lo que no hay riesgo de licenciamiento de assets.

## Instalación

```bash
python -m pip install -r requirements.txt
```

## Uso

### 1) Profesor (servidor LAN)

```bash
python -m sim_linea_prod --role teacher --seed 2026 --steps 15 --teacher-port 5050 --listen-seconds 60 --headless
```

### 2) Estudiante (cliente)

```bash
python -m sim_linea_prod --role student --teacher-host 192.168.1.10 --teacher-port 5050 --student-id est-01 --export-prefix outputs/est-01 --headless
```

Si no hay profesor disponible, el estudiante corre con su configuración local (`--seed`, `--steps`, etc.).

## Importar órdenes

Archivo CSV/Excel con columnas:

- `order_id`
- `quantity`

Ejemplo:

```csv
order_id,quantity
ORD-001,12
ORD-002,9
```

Ejecutar:

```bash
python -m sim_linea_prod --role student --import-file ordenes.csv --export-prefix outputs/resultado --headless
```

## Datos persistidos

SQLite (`--db`, por defecto `sim_linea_prod.sqlite`):

- `sessions`
- `events`
- `student_progress`
