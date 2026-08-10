const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("PracticeToken (ERC-20)", function () {
  async function deploy() {
    const [owner, alice, bob] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("PracticeToken");
    const initialSupply = ethers.parseEther("1000");
    const token = await Token.deploy(initialSupply);
    return { token, owner, alice, bob, initialSupply };
  }

  it("デプロイ時にownerへ初期供給量が発行される", async function () {
    const { token, owner, initialSupply } = await deploy();
    expect(await token.balanceOf(owner.address)).to.equal(initialSupply);
    expect(await token.totalSupply()).to.equal(initialSupply);
  });

  it("送金するとbalanceが正しく移動する", async function () {
    const { token, owner, alice } = await deploy();
    await token.transfer(alice.address, ethers.parseEther("100"));
    expect(await token.balanceOf(alice.address)).to.equal(ethers.parseEther("100"));
  });

  it("残高を超える送金は失敗する(フェーズ1の署名検証失敗に相当する安全装置)", async function () {
    const { token, alice, bob } = await deploy();
    await expect(
      token.connect(alice).transfer(bob.address, ethers.parseEther("1"))
    ).to.be.reverted;
  });

  it("owner以外はmintできない", async function () {
    const { token, alice } = await deploy();
    await expect(
      token.connect(alice).mint(alice.address, ethers.parseEther("1"))
    ).to.be.revertedWithCustomError(token, "OwnableUnauthorizedAccount");
  });

  it("ownerはmintできる", async function () {
    const { token, owner, alice, initialSupply } = await deploy();
    await token.mint(alice.address, ethers.parseEther("50"));
    expect(await token.balanceOf(alice.address)).to.equal(ethers.parseEther("50"));
    expect(await token.totalSupply()).to.equal(initialSupply + ethers.parseEther("50"));
  });
});
