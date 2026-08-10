// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// フェーズ1で自作した「署名付きトランザクション + 台帳」を、
/// OpenZeppelinのERC-20標準実装に置き換えて体感するための学習用トークン。
contract PracticeToken is ERC20, Ownable {
    constructor(uint256 initialSupply)
        ERC20("Practice Token", "PRAC")
        Ownable(msg.sender)
    {
        _mint(msg.sender, initialSupply);
    }

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }
}
