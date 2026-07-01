"""チェーン全体の管理: 生成、マイニング、検証"""
from block import Block
from transaction import Transaction

MINING_REWARD = 10.0


class Blockchain:
    def __init__(self, difficulty: int = 4) -> None:
        self.difficulty = difficulty
        self.pending_transactions: list[Transaction] = []
        self.chain: list[Block] = [self._create_genesis_block()]

    def _create_genesis_block(self) -> Block:
        genesis = Block(index=0, prev_hash="0" * 64, transactions=[])
        genesis.mine(self.difficulty)
        return genesis

    @property
    def latest_block(self) -> Block:
        return self.chain[-1]

    def add_transaction(self, transaction: Transaction) -> None:
        if not transaction.is_valid():
            raise ValueError("invalid transaction signature")
        self.pending_transactions.append(transaction)

    def mine_pending_transactions(self, miner_address: str) -> Block:
        reward_tx = Transaction(sender="COINBASE", recipient=miner_address, amount=MINING_REWARD)
        block = Block(
            index=self.latest_block.index + 1,
            prev_hash=self.latest_block.hash,
            transactions=[*self.pending_transactions, reward_tx],
        )
        block.mine(self.difficulty)
        self.chain.append(block)
        self.pending_transactions = []
        return block

    def is_valid(self) -> bool:
        return self.is_chain_valid(self.chain, self.difficulty)

    @staticmethod
    def is_chain_valid(chain: list[Block], difficulty: int) -> bool:
        if not chain:
            return False
        for i, block in enumerate(chain):
            if not block.is_valid(difficulty):
                return False
            if i > 0 and block.prev_hash != chain[i - 1].hash:
                return False  # 前のブロックとの連結が壊れている
        return True

    def replace_chain(self, new_chain: list[Block]) -> bool:
        """最長チェーン優先ルール: より長く、かつ有効なチェーンであれば採用する"""
        if len(new_chain) <= len(self.chain):
            return False
        if not self.is_chain_valid(new_chain, self.difficulty):
            return False
        self.chain = new_chain
        return True
