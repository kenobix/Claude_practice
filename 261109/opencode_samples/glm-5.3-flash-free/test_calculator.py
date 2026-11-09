"""calculator.py の unittest テスト: 正常系と異常系を網羅。"""

import unittest

from calculator import add, divide, multiply, subtract


class TestNormalCases(unittest.TestCase):
    """正常系のテスト。"""

    def test_add(self):
        self.assertEqual(add(2, 3), 5)
        self.assertEqual(add(-1, 1), 0)

    def test_add_float(self):
        self.assertAlmostEqual(add(0.1, 0.2), 0.3)

    def test_subtract(self):
        self.assertEqual(subtract(5, 3), 2)
        self.assertEqual(subtract(3, 5), -2)

    def test_multiply(self):
        self.assertEqual(multiply(4, 3), 12)
        self.assertEqual(multiply(-2, 3), -6)

    def test_divide(self):
        self.assertEqual(divide(10, 2), 5.0)
        self.assertAlmostEqual(divide(1, 3), 0.3333333, places=6)


class TestErrorCases(unittest.TestCase):
    """異常系のテスト。"""

    def test_divide_by_zero_raises(self):
        with self.assertRaises(ZeroDivisionError):
            divide(10, 0)

    def test_divide_by_zero_message(self):
        with self.assertRaises(ZeroDivisionError) as ctx:
            divide(10, 0)
        self.assertIn("ゼロ除算", str(ctx.exception))

    def test_divide_zero_by_zero_raises(self):
        with self.assertRaises(ZeroDivisionError):
            divide(0, 0)


if __name__ == "__main__":
    unittest.main()
