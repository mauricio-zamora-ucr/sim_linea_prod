import tempfile
import unittest

from sim_linea_prod.main import Order, SQLiteStore, SimulationConfig, run_simulation


class SimulationSeedTests(unittest.TestCase):
    def test_same_seed_produces_same_events(self):
        cfg = SimulationConfig(seed=42, steps=3, process_time=0.01)
        orders = [Order("A", 10), Order("B", 8), Order("C", 6)]

        with tempfile.TemporaryDirectory() as tmp:
            db1 = SQLiteStore(f"{tmp}/a.sqlite")
            db2 = SQLiteStore(f"{tmp}/b.sqlite")

            events1 = run_simulation(
                config=cfg,
                orders=orders,
                session_id=db1.create_session("student", cfg),
                store=db1,
                role="student",
                student_id="s1",
                teacher_host=None,
                teacher_port=5050,
            )
            events2 = run_simulation(
                config=cfg,
                orders=orders,
                session_id=db2.create_session("student", cfg),
                store=db2,
                role="student",
                student_id="s2",
                teacher_host=None,
                teacher_port=5050,
            )

            self.assertEqual(events1, events2)

    def test_different_seed_changes_events(self):
        cfg1 = SimulationConfig(seed=42, steps=3, process_time=0.01)
        cfg2 = SimulationConfig(seed=43, steps=3, process_time=0.01)
        orders = [Order("A", 10), Order("B", 8), Order("C", 6)]

        with tempfile.TemporaryDirectory() as tmp:
            db1 = SQLiteStore(f"{tmp}/a.sqlite")
            db2 = SQLiteStore(f"{tmp}/b.sqlite")

            events1 = run_simulation(
                config=cfg1,
                orders=orders,
                session_id=db1.create_session("student", cfg1),
                store=db1,
                role="student",
                student_id="s1",
                teacher_host=None,
                teacher_port=5050,
            )
            events2 = run_simulation(
                config=cfg2,
                orders=orders,
                session_id=db2.create_session("student", cfg2),
                store=db2,
                role="student",
                student_id="s2",
                teacher_host=None,
                teacher_port=5050,
            )

            self.assertNotEqual(events1, events2)


if __name__ == "__main__":
    unittest.main()
