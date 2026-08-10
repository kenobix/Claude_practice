const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("ProvenanceRegistry", function () {
  async function deploy() {
    const [alice, bob] = await ethers.getSigners();
    const Registry = await ethers.getContractFactory("ProvenanceRegistry");
    const registry = await Registry.deploy();
    const recordHash = ethers.keccak256(ethers.toUtf8Bytes("dummy-ai-verdict"));
    return { registry, alice, bob, recordHash };
  }

  it("未登録のハッシュはisRegisteredがfalseを返す", async function () {
    const { registry, recordHash } = await deploy();
    expect(await registry.isRegistered(recordHash)).to.equal(false);
  });

  it("登録するとisRegisteredがtrueになり、submitter/metadataが取得できる", async function () {
    const { registry, alice, recordHash } = await deploy();
    await expect(registry.connect(alice).register(recordHash, "verdict=承認"))
      .to.emit(registry, "RecordRegistered")
      .withArgs(recordHash, alice.address, "verdict=承認");

    expect(await registry.isRegistered(recordHash)).to.equal(true);
    const record = await registry.getRecord(recordHash);
    expect(record.submitter).to.equal(alice.address);
    expect(record.metadata).to.equal("verdict=承認");
  });

  it("同じハッシュを二重登録すると失敗する(改ざん後の再登録による上書きを防ぐ)", async function () {
    const { registry, alice, recordHash } = await deploy();
    await registry.register(recordHash, "verdict=承認");
    await expect(registry.connect(alice).register(recordHash, "verdict=却下"))
      .to.be.revertedWithCustomError(registry, "AlreadyRegistered")
      .withArgs(recordHash);
  });

  it("未登録のハッシュをgetRecordすると失敗する", async function () {
    const { registry, recordHash } = await deploy();
    await expect(registry.getRecord(recordHash))
      .to.be.revertedWithCustomError(registry, "NotRegistered")
      .withArgs(recordHash);
  });

  it("改ざんされたデータから計算したハッシュは元のレコードとは別物として扱われる", async function () {
    const { registry, recordHash } = await deploy();
    await registry.register(recordHash, "verdict=承認");
    const tamperedHash = ethers.keccak256(ethers.toUtf8Bytes("dummy-ai-verdict-tampered"));
    expect(await registry.isRegistered(tamperedHash)).to.equal(false);
  });
});
