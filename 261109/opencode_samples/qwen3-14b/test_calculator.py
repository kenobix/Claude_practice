import unittest
import calculator

class TestCalculator(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calculator.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(calculator.subtract(5, 2), 3)

    def test_multiply(self):
        self.assertEqual(calculator.multiply(4, 5), 20)

    def test_divide_normal(self):
        self.assertEqual(calculator.divide(10, 2), 5)

    def test_divide_zero(self):
        with self.assertRaises(ZeroDivisionError):
            calculator.divide(5, 0)

if __name__ == "__main__":
    unittest.main()