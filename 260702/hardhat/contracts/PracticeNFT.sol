// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// フェーズ1のブロックチェーンは「同じ形式のトランザクション」しか扱わなかったが、
/// ERC-721では1つ1つのトークンが固有のID・所有者・メタデータを持つことを体感する学習用NFT。
contract PracticeNFT is ERC721, Ownable {
    uint256 private _nextTokenId;

    constructor() ERC721("Practice NFT", "PNFT") Ownable(msg.sender) {}

    function safeMint(address to) external onlyOwner returns (uint256) {
        uint256 tokenId = _nextTokenId++;
        _safeMint(to, tokenId);
        return tokenId;
    }
}
