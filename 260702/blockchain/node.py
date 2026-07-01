"""簡易P2P: 実ネットワークは使わず、同一プロセス内で複数ノードの同期を再現する"""
from blockchain import Blockchain


class Node:
    def __init__(self, name: str, difficulty: int = 4) -> None:
        self.name = name
        self.blockchain = Blockchain(difficulty=difficulty)
        self.peers: list["Node"] = []

    def connect(self, peer: "Node") -> None:
        if peer not in self.peers:
            self.peers.append(peer)
            peer.connect(self)

    def broadcast_chain(self) -> None:
        """自分のチェーンを全ピアに送り、最長チェーン優先ルールで各ピアに反映させる"""
        for peer in self.peers:
            peer.receive_chain(self.blockchain.chain)

    def receive_chain(self, chain: list) -> bool:
        return self.blockchain.replace_chain(chain)
