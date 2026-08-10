const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("PracticeNFT (ERC-721)", function () {
  async function deploy() {
    const [owner, alice, bob] = await ethers.getSigners();
    const NFT = await ethers.getContractFactory("PracticeNFT");
    const nft = await NFT.deploy();
    return { nft, owner, alice, bob };
  }

  it("mintするとtokenIdが0から連番で発行され、所有者が記録される", async function () {
    const { nft, owner, alice } = await deploy();
    await nft.safeMint(alice.address);
    await nft.safeMint(alice.address);
    expect(await nft.ownerOf(0)).to.equal(alice.address);
    expect(await nft.ownerOf(1)).to.equal(alice.address);
    expect(await nft.balanceOf(alice.address)).to.equal(2);
  });

  it("owner以外はmintできない", async function () {
    const { nft, alice } = await deploy();
    await expect(
      nft.connect(alice).safeMint(alice.address)
    ).to.be.revertedWithCustomError(nft, "OwnableUnauthorizedAccount");
  });

  it("各tokenIdは固有であり、ERC-20のように数量として混ざらない", async function () {
    const { nft, alice, bob } = await deploy();
    await nft.safeMint(alice.address);
    await nft.connect(alice).transferFrom(alice.address, bob.address, 0);
    expect(await nft.ownerOf(0)).to.equal(bob.address);
    expect(await nft.balanceOf(alice.address)).to.equal(0);
  });
});
