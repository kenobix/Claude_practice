const { ethers } = require("hardhat");

async function main() {
  const [deployer, alice] = await ethers.getSigners();
  console.log("デプロイアカウント:", deployer.address);

  const Token = await ethers.getContractFactory("PracticeToken");
  const token = await Token.deploy(ethers.parseEther("1000"));
  await token.waitForDeployment();
  console.log("PracticeToken (ERC-20) デプロイ先:", await token.getAddress());

  const NFT = await ethers.getContractFactory("PracticeNFT");
  const nft = await NFT.deploy();
  await nft.waitForDeployment();
  console.log("PracticeNFT (ERC-721) デプロイ先:", await nft.getAddress());

  const transferTx = await token.transfer(alice.address, ethers.parseEther("100"));
  await transferTx.wait();
  console.log(`ERC-20: ${deployer.address} → ${alice.address} へ100 PRACを送金`);
  console.log("alice残高:", ethers.formatEther(await token.balanceOf(alice.address)), "PRAC");

  const mintTx = await nft.safeMint(alice.address);
  await mintTx.wait();
  console.log(`ERC-721: alice(${alice.address}) へ tokenId=0 をmint`);
  console.log("tokenId=0 の所有者:", await nft.ownerOf(0));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
