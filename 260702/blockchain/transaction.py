"""ECDSA(SECP256K1)による署名付きトランザクション"""
import json
from dataclasses import dataclass, field
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    encode_dss_signature,
    decode_dss_signature,
)
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature


def generate_keypair() -> tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey]:
    private_key = ec.generate_private_key(ec.SECP256K1())
    return private_key, private_key.public_key()


def public_key_to_hex(public_key: ec.EllipticCurvePublicKey) -> str:
    numbers = public_key.public_numbers()
    return f"{numbers.x:064x}{numbers.y:064x}"


@dataclass
class Transaction:
    sender: str  # 公開鍵のhex文字列（"COINBASE"は採掘報酬）
    recipient: str
    amount: float
    signature: str | None = field(default=None, repr=False)

    def payload(self) -> str:
        return json.dumps(
            {"sender": self.sender, "recipient": self.recipient, "amount": self.amount},
            sort_keys=True,
        )

    def sign(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        der_sig = private_key.sign(self.payload().encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_sig)
        self.signature = f"{r:064x}{s:064x}"

    def is_valid(self) -> bool:
        if self.sender == "COINBASE":
            return True  # 採掘報酬は署名不要
        if not self.signature:
            return False
        try:
            x = int(self.sender[:64], 16)
            y = int(self.sender[64:128], 16)
            public_key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256K1()).public_key()
            r = int(self.signature[:64], 16)
            s = int(self.signature[64:128], 16)
            der_sig = encode_dss_signature(r, s)
            public_key.verify(der_sig, self.payload().encode(), ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError):
            return False

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "signature": self.signature,
        }
