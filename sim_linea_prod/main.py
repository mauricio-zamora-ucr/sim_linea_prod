from __future__ import annotations

import argparse
import json
import random
import socket
import socketserver
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import simpy


@dataclass
class Order:
    order_id: str
    quantity: int


@dataclass
class SimulationConfig:
    seed: int
    steps: int
    process_time: float = 1.0
    rework_probability: float = 0.15
    initial_inventory: int = 50


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    steps INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    ts REAL NOT NULL,
                    order_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    good_qty INTEGER NOT NULL,
                    rework_qty INTEGER NOT NULL,
                    inventory INTEGER NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS student_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    def create_session(self, role: str, config: SimulationConfig) -> int:
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO sessions(role, started_at, seed, steps) VALUES (?, ?, ?, ?)",
                (role, datetime.now(UTC).isoformat(), config.seed, config.steps),
            )
            return int(cur.lastrowid)

    def save_event(self, session_id: int, event: Dict[str, object]) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO events(session_id, ts, order_id, status, good_qty, rework_qty, inventory)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    float(event["ts"]),
                    str(event["order_id"]),
                    str(event["status"]),
                    int(event["good_qty"]),
                    int(event["rework_qty"]),
                    int(event["inventory"]),
                ),
            )

    def save_student_progress(self, student_id: str, payload: Dict[str, object]) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO student_progress(student_id, received_at, payload) VALUES (?, ?, ?)",
                (student_id, datetime.now(UTC).isoformat(), json.dumps(payload, ensure_ascii=False)),
            )


def load_orders(path: Optional[str], seed: int, steps: int) -> List[Order]:
    if path:
        source = Path(path)
        if source.suffix.lower() in {".xlsx", ".xls"}:
            df = pd.read_excel(source)
        else:
            df = pd.read_csv(source)
        required = {"order_id", "quantity"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Archivo de órdenes inválido, faltan columnas: {sorted(missing)}")
        return [Order(str(r["order_id"]), int(r["quantity"])) for _, r in df.iterrows()]

    rng = random.Random(seed)
    return [Order(f"ORD-{i+1:03d}", rng.randint(6, 14)) for i in range(steps)]


def export_results(events: Iterable[Dict[str, object]], prefix: Optional[str]) -> None:
    if not prefix:
        return

    df = pd.DataFrame(list(events))
    output_prefix = Path(prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(f"{output_prefix}.csv", index=False)
    df.to_excel(f"{output_prefix}.xlsx", index=False)


class TeacherServer:
    def __init__(self, host: str, port: int, config: SimulationConfig, store: SQLiteStore):
        self.config = config
        self.store = store
        self.student_state: Dict[str, Dict[str, object]] = {}

        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:  # type: ignore[override]
                for raw in self.rfile:
                    message = json.loads(raw.decode("utf-8"))
                    msg_type = message.get("type")
                    if msg_type == "config_request":
                        payload = {
                            "type": "config",
                            "seed": outer.config.seed,
                            "steps": outer.config.steps,
                            "process_time": outer.config.process_time,
                            "rework_probability": outer.config.rework_probability,
                            "initial_inventory": outer.config.initial_inventory,
                        }
                        self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
                        self.wfile.flush()
                    elif msg_type == "progress":
                        student_id = str(message.get("student_id", "unknown"))
                        outer.student_state[student_id] = message
                        outer.store.save_student_progress(student_id, message)

        self.server = socketserver.ThreadingTCPServer((host, port), Handler)
        self.server.daemon_threads = True

    def start(self) -> None:
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()


def request_config(host: str, port: int) -> Optional[SimulationConfig]:
    try:
        with socket.create_connection((host, port), timeout=3) as conn:
            conn.sendall(json.dumps({"type": "config_request"}).encode("utf-8") + b"\n")
            response = conn.recv(4096).decode("utf-8").strip()
            if not response:
                return None
            payload = json.loads(response)
            if payload.get("type") != "config":
                return None
            return SimulationConfig(
                seed=int(payload["seed"]),
                steps=int(payload["steps"]),
                process_time=float(payload["process_time"]),
                rework_probability=float(payload["rework_probability"]),
                initial_inventory=int(payload["initial_inventory"]),
            )
    except OSError:
        return None


def send_progress(host: str, port: int, message: Dict[str, object]) -> None:
    try:
        with socket.create_connection((host, port), timeout=1) as conn:
            conn.sendall(json.dumps(message).encode("utf-8") + b"\n")
    except OSError:
        pass


def run_simulation(
    config: SimulationConfig,
    orders: List[Order],
    session_id: int,
    store: SQLiteStore,
    role: str,
    student_id: str,
    teacher_host: Optional[str],
    teacher_port: int,
) -> List[Dict[str, object]]:
    rng = random.Random(config.seed)
    env = simpy.Environment()
    machine = simpy.Resource(env, capacity=1)

    state = {
        "inventory": config.initial_inventory,
        "completed": 0,
        "rework": 0,
    }
    events: List[Dict[str, object]] = []

    def process_order(order: Order):
        with machine.request() as req:
            yield req
            yield env.timeout(config.process_time)

            rejected = rng.random() < config.rework_probability
            good_qty = order.quantity if not rejected else max(1, order.quantity - 1)
            rework_qty = 0 if not rejected else 1
            status = "done" if not rejected else "rework"

            state["inventory"] += good_qty - order.quantity
            state["completed"] += good_qty
            state["rework"] += rework_qty

            event = {
                "ts": env.now,
                "order_id": order.order_id,
                "status": status,
                "good_qty": good_qty,
                "rework_qty": rework_qty,
                "inventory": state["inventory"],
            }
            events.append(event)
            store.save_event(session_id, event)

            if role == "student" and teacher_host:
                send_progress(
                    teacher_host,
                    teacher_port,
                    {
                        "type": "progress",
                        "student_id": student_id,
                        "step": len(events),
                        "order_id": order.order_id,
                        "status": status,
                        "inventory": state["inventory"],
                        "completed": state["completed"],
                        "rework": state["rework"],
                    },
                )

    for order in orders[: config.steps]:
        env.process(process_order(order))

    env.run()
    return events


def maybe_show_pygame_dashboard(headless: bool, title: str, summary: Dict[str, int]) -> None:
    if headless:
        print(f"[{title}] resumen: {summary}")
        return

    import pygame

    pygame.init()
    screen = pygame.display.set_mode((800, 320))
    pygame.display.set_caption(title)
    font = pygame.font.SysFont(None, 28)
    clock = pygame.time.Clock()

    running = True
    shown_until = time.time() + 6

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        if time.time() > shown_until:
            running = False

        screen.fill((30, 30, 35))

        labels = [
            ("Completadas", summary["completed"], (76, 175, 80)),
            ("Reproceso", summary["rework"], (255, 152, 0)),
            ("Inventario", summary["inventory"], (33, 150, 243)),
        ]

        for idx, (name, value, color) in enumerate(labels):
            y = 40 + idx * 90
            pygame.draw.rect(screen, color, pygame.Rect(40, y, max(1, value * 4), 40))
            txt = font.render(f"{name}: {value}", True, (240, 240, 240))
            screen.blit(txt, (40, y - 24))

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Juego/simulador de línea de producción para cursos de ingeniería industrial."
    )
    parser.add_argument("--role", choices=["teacher", "student"], default="student")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--process-time", type=float, default=1.0)
    parser.add_argument("--rework-probability", type=float, default=0.15)
    parser.add_argument("--initial-inventory", type=int, default=50)
    parser.add_argument("--import-file", type=str)
    parser.add_argument("--export-prefix", type=str)
    parser.add_argument("--db", type=str, default="sim_linea_prod.sqlite")
    parser.add_argument("--headless", action="store_true")

    parser.add_argument("--teacher-host", type=str, default="127.0.0.1")
    parser.add_argument("--teacher-port", type=int, default=5050)
    parser.add_argument("--student-id", type=str, default="estudiante-1")

    parser.add_argument("--listen-host", type=str, default="0.0.0.0")
    parser.add_argument("--listen-seconds", type=int, default=30)

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    local_config = SimulationConfig(
        seed=args.seed,
        steps=args.steps,
        process_time=args.process_time,
        rework_probability=args.rework_probability,
        initial_inventory=args.initial_inventory,
    )

    store = SQLiteStore(args.db)

    if args.role == "teacher":
        server = TeacherServer(args.listen_host, args.teacher_port, local_config, store)
        server.start()
        print(
            f"Profesor escuchando en {args.listen_host}:{args.teacher_port} durante {args.listen_seconds}s"
        )
        end = time.time() + args.listen_seconds
        while time.time() < end:
            time.sleep(0.25)

        summary = {
            "completed": 0,
            "rework": 0,
            "inventory": 0,
        }
        if server.student_state:
            latest = list(server.student_state.values())[-1]
            summary = {
                "completed": int(latest.get("completed", 0)),
                "rework": int(latest.get("rework", 0)),
                "inventory": int(latest.get("inventory", 0)),
            }
        maybe_show_pygame_dashboard(args.headless, "Panel de profesor", summary)
        return

    teacher_config = request_config(args.teacher_host, args.teacher_port)
    config = teacher_config or local_config

    session_id = store.create_session("student", config)
    orders = load_orders(args.import_file, seed=config.seed, steps=config.steps)
    events = run_simulation(
        config=config,
        orders=orders,
        session_id=session_id,
        store=store,
        role="student",
        student_id=args.student_id,
        teacher_host=args.teacher_host if teacher_config else None,
        teacher_port=args.teacher_port,
    )

    export_results(events, args.export_prefix)

    summary = {
        "completed": sum(int(e["good_qty"]) for e in events),
        "rework": sum(int(e["rework_qty"]) for e in events),
        "inventory": int(events[-1]["inventory"]) if events else config.initial_inventory,
    }
    maybe_show_pygame_dashboard(args.headless, "Simulador de producción", summary)


if __name__ == "__main__":
    main()
