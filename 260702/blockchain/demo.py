"""フェーズ1のデモ: マイニング、署名検証、改ざん検知、フォーク解消を一通り実行する"""
from blockchain import Blockchain
from node import Node
from transaction import Transaction, generate_keypair, public_key_to_hex


def section(title: str) -> None:
    print(f"\n{'=' * 10} {title} {'=' * 10}")


def demo_mining_and_transactions() -> None:
    section("1. マイニングと署名付きトランザクション")
    chain = Blockchain(difficulty=4)

    alice_priv, alice_pub = generate_keypair()
    bob_priv, bob_pub = generate_keypair()
    alice_addr = public_key_to_hex(alice_pub)
    bob_addr = public_key_to_hex(bob_pub)

    tx = Transaction(sender=alice_addr, recipient=bob_addr, amount=5.0)
    tx.sign(alice_priv)
    print(f"署名検証(正常なトランザクション): {tx.is_valid()}")
    chain.add_transaction(tx)

    tampered = Transaction(sender=alice_addr, recipient=bob_addr, amount=999.0, signature=tx.signature)
    print(f"署名検証(金額を改ざんしたトランザクション): {tampered.is_valid()}")

    block = chain.mine_pending_transactions(miner_address=alice_addr)
    print(f"ブロック#{block.index}をマイニング: hash={block.hash[:16]}... nonce={block.nonce}")
    print(f"チェーンの検証結果: {chain.is_valid()}")


def demo_tamper_detection() -> None:
    section("2. 改ざん検知")
    chain = Blockchain(difficulty=4)
    for i in range(3):
        chain.mine_pending_transactions(miner_address="miner")
    print(f"改ざん前のチェーン検証: {chain.is_valid()}")

    chain.chain[1].transactions.append(
        Transaction(sender="COINBASE", recipient="attacker", amount=1000.0)
    )
    print(f"ブロック#1のデータを直接書き換えた後の検証: {chain.is_valid()}")


def demo_fork_resolution() -> None:
    section("3. 複数ノードでのフォーク解消(最長チェーン優先)")
    node_a = Node("NodeA", difficulty=4)
    node_b = Node("NodeB", difficulty=4)
    node_a.connect(node_b)

    # 2ノードがそれぞれ独立にマイニングし、一時的にチェーンが分岐(フォーク)する
    node_a.blockchain.mine_pending_transactions(miner_address="A")
    node_b.blockchain.mine_pending_transactions(miner_address="B")
    node_b.blockchain.mine_pending_transactions(miner_address="B")
    print(f"NodeA chain length: {len(node_a.blockchain.chain)}")
    print(f"NodeB chain length: {len(node_b.blockchain.chain)}")

    node_b.broadcast_chain()
    print("NodeBがブロードキャスト → 最長チェーン優先ルールでNodeAが追従")
    print(f"NodeA chain length: {len(node_a.blockchain.chain)}")
    print(f"NodeAとNodeBのチェーンが一致: {[b.hash for b in node_a.blockchain.chain] == [b.hash for b in node_b.blockchain.chain]}")


if __name__ == "__main__":
    demo_mining_and_transactions()
    demo_tamper_detection()
    demo_fork_resolution()
