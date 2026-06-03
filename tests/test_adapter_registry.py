import tempfile
import unittest
from pathlib import Path

import app.adapters.adobe_car  # noqa: F401
from app.adapters import registry


class AdapterRegistryTest(unittest.TestCase):
    def test_load_supplier_configs_skips_support_yaml_without_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "real_supplier.yaml").write_text(
                "\n".join(
                    [
                        "id: adobe_car",
                        "name: AdobeCar",
                        "enabled: true",
                        "supports_one_way: false",
                        'countries: ["CR"]',
                    ]
                ),
                encoding="utf-8",
            )
            (config_dir / "support_data.yaml").write_text(
                "locations:\n- code: MXP\n  name: Milan Malpensa Airport\n",
                encoding="utf-8",
            )

            configs = registry.load_supplier_configs(str(config_dir))
            suppliers = registry.list_suppliers()

        self.assertEqual(["adobe_car"], list(configs.keys()))
        self.assertEqual(1, len(suppliers))
        self.assertEqual("adobe_car", suppliers[0]["id"])
        self.assertTrue(suppliers[0]["has_adapter"])
        self.assertTrue(suppliers[0]["supports_one_way"])
        self.assertFalse(suppliers[0]["configured_supports_one_way"])
        self.assertTrue(suppliers[0]["supports_one_way_mismatch"])


if __name__ == "__main__":
    unittest.main()
