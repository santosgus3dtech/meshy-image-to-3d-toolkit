import unittest

from src.mesh_quality import assess


class MeshQualityTests(unittest.TestCase):
    def test_clean_mesh_is_ready(self):
        result = assess(
            {
                "watertight": True,
                "components": 1,
                "volume_cm3": 42.5,
                "thin_wall_ratio": 0.01,
                "bounds_mm": [82, 64, 115],
            }
        )
        self.assertEqual(result.score, 100)
        self.assertEqual(result.status, "ready")

    def test_open_multi_component_mesh_requires_repair(self):
        result = assess(
            {
                "watertight": False,
                "components": 8,
                "volume_cm3": 0,
                "thin_wall_ratio": 0.25,
                "bounds_mm": [420, 55, 70],
            }
        )
        self.assertEqual(result.status, "repair")
        self.assertEqual(len(result.recommendations), 5)


if __name__ == "__main__":
    unittest.main()
