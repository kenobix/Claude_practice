import { AbsoluteFill, Sequence } from "remotion";
import { TitleScene } from "./scenes/TitleScene";
import { OverviewScene } from "./scenes/OverviewScene";
import { ConnectorsScene } from "./scenes/ConnectorsScene";
import { DemoScene } from "./scenes/DemoScene";
import { SummaryScene } from "./scenes/SummaryScene";

// シーン構成
// Scene 1: タイトル          0〜 89フレーム  ( 0〜 2秒)
// Scene 2: Claude Codeとは  90〜239フレーム  ( 3〜 7秒)
// Scene 3: コネクタ一覧     240〜479フレーム  ( 8〜15秒)
// Scene 4: デモイメージ     480〜659フレーム  (16〜21秒)
// Scene 5: まとめ           660〜839フレーム  (22〜27秒)

export const ClaudeCodeVideo = () => {
  return (
    <AbsoluteFill className="bg-gray-950 text-white font-sans">
      <Sequence from={0} durationInFrames={90}>
        <TitleScene />
      </Sequence>

      <Sequence from={90} durationInFrames={150}>
        <OverviewScene />
      </Sequence>

      <Sequence from={240} durationInFrames={240}>
        <ConnectorsScene />
      </Sequence>

      <Sequence from={480} durationInFrames={180}>
        <DemoScene />
      </Sequence>

      <Sequence from={660} durationInFrames={180}>
        <SummaryScene />
      </Sequence>
    </AbsoluteFill>
  );
};
