"""小さな計算アプリ: Python標準ライブラリのみで実装。"""


def add(a, b):
    """加算: a + b を返す。"""
    return a + b


def subtract(a, b):
    """減算: a - b を返す。"""
    return a - b


def multiply(a, b):
    """乗算: a * b を返す。"""
    return a * b


def divide(a, b):
    """除算: a / b を返す。b が 0 の場合は ZeroDivisionError を発生させる。"""
    if b == 0:
        raise ZeroDivisionError("ゼロ除算は許可されていません")
    return a / b
