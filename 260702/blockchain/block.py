"""ブロック構造とProof of Work"""
import hashlib
import json
import time
from dataclasses import dataclass, field

from transaction import Transaction


@dataclass
class Block:
    index: int
    prev_hash: str
    transactions: list[Transaction]
    timestamp: float = field(default_factory=time.time)
    nonce: int = 0
    hash: str = field(default="", repr=False)

    def compute_hash(self) -> str:
        payload = json.dumps(
            {
                "index": self.index,
                "prev_hash": self.prev_hash,
                "transactions": [t.to_dict() for t in self.transactions],
                "timestamp": self.timestamp,
                "nonce": self.nonce,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def mine(self, difficulty: int) -> None:
        target = "0" * difficulty
        self.hash = self.compute_hash()
        while not self.hash.startswith(target):
            self.nonce += 1
            self.hash = self.compute_hash()

    def is_valid(self, difficulty: int) -> bool:
        if self.hash != self.compute_hash():
            return False  # データが改ざんされている
        if not self.hash.startswith("0" * difficulty):
            return False  # PoWを満たしていない
        return all(t.is_valid() for t in self.transactions)
