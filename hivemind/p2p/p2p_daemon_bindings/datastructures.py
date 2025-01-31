"""
Originally taken from: https://github.com/mhchia/py-libp2p-daemon-bindings
Licence: MIT
Author: Kevin Mai-Husan Chia
"""

import hashlib
from typing import Any, Sequence, Union

import base58
import multihash
from cryptography.hazmat.primitives import serialization
from multiaddr import Multiaddr, protocols

from hivemind.proto import crypto_pb2, p2pd_pb2


class PeerID:
    def __init__(self, peer_id_bytes: bytes) -> None:
        self._bytes = peer_id_bytes
        self._xor_id = int(sha256_digest(self._bytes).hex(), 16)
        self._b58_str = base58.b58encode(self._bytes).decode()

    @property
    def xor_id(self) -> int:
        return self._xor_id

    def to_bytes(self) -> bytes:
        return self._bytes

    def to_base58(self) -> str:
        return self._b58_str

    def __repr__(self) -> str:
        return f"<libp2p.peer.id.ID ({self.to_base58()})>"

    def __str__(self):
        return self.to_base58()

    def pretty(self):
        return self.to_base58()

    def to_string(self):
        return self.to_base58()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.to_base58() == other
        elif isinstance(other, bytes):
            return self._bytes == other
        elif isinstance(other, PeerID):
            return self._bytes == other._bytes
        else:
            return False

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, PeerID):
            raise TypeError(f"'<' not supported between instances of 'PeerID' and '{type(other)}'")

        return self.to_base58() < other.to_base58()

    def __hash__(self) -> int:
        return hash(self._bytes)

    @classmethod
    def from_base58(cls, base58_id: str) -> "PeerID":
        peer_id_bytes = base58.b58decode(base58_id)
        return cls(peer_id_bytes)

    @classmethod
    def from_identity(cls, data: bytes) -> "PeerID":
        """
        See [1] for the specification of how this conversion should happen.

        [1] https://github.com/libp2p/specs/blob/master/peer-ids/peer-ids.md#peer-ids
        """
        key_data = crypto_pb2.PrivateKey.FromString(data).data
        private_key = serialization.load_der_private_key(key_data, password=None)

        encoded_public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        encoded_public_key = crypto_pb2.PublicKey(
            key_type=crypto_pb2.RSA,
            data=encoded_public_key,
        ).SerializeToString()

        encoded_digest = multihash.encode(
            hashlib.sha256(encoded_public_key).digest(),
            multihash.coerce_code("sha2-256"),
        )
        return cls(encoded_digest)


def sha256_digest(data: Union[str, bytes]) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf8")
    return hashlib.sha256(data).digest()


class StreamInfo:
    def __init__(self, peer_id: PeerID, addr: Multiaddr, proto: str) -> None:
        self.peer_id = peer_id
        self.addr = addr
        self.proto = proto

    def __repr__(self) -> str:
        return f"<StreamInfo peer_id={self.peer_id} addr={self.addr} proto={self.proto}>"

    def to_protobuf(self) -> p2pd_pb2.StreamInfo:
        pb_msg = p2pd_pb2.StreamInfo(peer=self.peer_id.to_bytes(), addr=self.addr.to_bytes(), proto=self.proto)
        return pb_msg

    @classmethod
    def from_protobuf(cls, pb_msg: p2pd_pb2.StreamInfo) -> "StreamInfo":
        stream_info = cls(peer_id=PeerID(pb_msg.peer), addr=Multiaddr(pb_msg.addr), proto=pb_msg.proto)
        return stream_info


class PeerInfo:
    def __init__(self, peer_id: PeerID, addrs: Sequence[Multiaddr], rate_in=0, rate_out=0) -> None:
        self.peer_id = peer_id
        self.addrs = list(addrs)
        self.rate_in = rate_in
        self.rate_out = rate_out

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, PeerInfo) and self.peer_id == other.peer_id and self.addrs == other.addrs

    @classmethod
    def from_protobuf(cls, peer_info_pb: p2pd_pb2.PeerInfo) -> "PeerInfo":
        peer_id = PeerID(peer_info_pb.id)
        addrs = [Multiaddr(addr) for addr in peer_info_pb.addrs]
        rate_in = peer_info_pb.ratein
        rate_out = peer_info_pb.rateout
        return PeerInfo(peer_id, addrs, rate_in, rate_out)

    def __str__(self):
        return f"{self.peer_id.pretty()} {','.join(str(a) for a in self.addrs)}, rate_in = {repr(self.rate_in)}, rate_out = {repr(self.rate_out)})"

    def __repr__(self):
        return f"PeerInfo(peer_id={repr(self.peer_id)}, addrs={repr(self.addrs)}, rate_in = {repr(self.rate_in)}, rate_out = {repr(self.rate_out)})"


class InvalidAddrError(ValueError):
    pass


def info_from_p2p_addr(addr: Multiaddr) -> PeerInfo:
    if addr is None:
        raise InvalidAddrError("`addr` should not be `None`")

    parts = addr.split()
    if not parts:
        raise InvalidAddrError(f"`parts`={parts} should at least have a protocol `P_P2P`")

    p2p_part = parts[-1]
    last_protocol_code = p2p_part.protocols()[0].code
    if last_protocol_code != protocols.P_P2P:
        raise InvalidAddrError(f"The last protocol should be `P_P2P` instead of `{last_protocol_code}`")

    # make sure the /p2p value parses as a peer.ID
    peer_id_str: str = p2p_part.value_for_protocol(protocols.P_P2P)
    peer_id = PeerID.from_base58(peer_id_str)

    # we might have received just an / p2p part, which means there's no addr.
    if len(parts) > 1:
        addr = Multiaddr.join(*parts[:-1])

    return PeerInfo(peer_id, [addr])


class BandwidthMetrics:
    def __init__(self, rateIn: int = 0, rateOut: int = 0, peers: Sequence[PeerID] = []) -> None:
        self.selfRateIn = rateIn
        self.selfRateOut = rateOut
        self.peers = peers

    @classmethod
    def from_protobuf(cls, bmr: p2pd_pb2.BandwidthMetricsResponse) -> "BandwidthMetrics":
        rate_in = bmr.selfRateIn
        rate_out = bmr.selfRateOut
        peers = [PeerInfo.from_protobuf(peer_info) for peer_info in bmr.peers]
        return BandwidthMetrics(rate_in, rate_out, peers)
