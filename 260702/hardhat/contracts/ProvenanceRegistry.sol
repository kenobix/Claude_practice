// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// フェーズ3: オフチェーンでのAI(Gemini API)による解析・判定結果のハッシュを
/// オンチェーンに記録し、改ざん耐性と証跡(プロブナンス)を与えるレジストリ。
/// コントラクト自体は生データを保持せず、ハッシュと最小限のメタデータのみを扱う。
contract ProvenanceRegistry {
    struct Record {
        address submitter;
        uint256 timestamp;
        string metadata;
    }

    mapping(bytes32 => Record) private _records;

    event RecordRegistered(bytes32 indexed recordHash, address indexed submitter, string metadata);

    error AlreadyRegistered(bytes32 recordHash);
    error NotRegistered(bytes32 recordHash);

    /// @param recordHash オフチェーンで計算したAI判定結果(+メタデータ)のSHA-256ハッシュ
    /// @param metadata 人間が読める要約(判定内容の短い説明等)
    function register(bytes32 recordHash, string calldata metadata) external {
        if (_records[recordHash].timestamp != 0) {
            revert AlreadyRegistered(recordHash);
        }
        _records[recordHash] = Record(msg.sender, block.timestamp, metadata);
        emit RecordRegistered(recordHash, msg.sender, metadata);
    }

    function isRegistered(bytes32 recordHash) external view returns (bool) {
        return _records[recordHash].timestamp != 0;
    }

    function getRecord(bytes32 recordHash)
        external
        view
        returns (address submitter, uint256 timestamp, string memory metadata)
    {
        Record memory r = _records[recordHash];
        if (r.timestamp == 0) revert NotRegistered(recordHash);
        return (r.submitter, r.timestamp, r.metadata);
    }
}
