import { Composition } from "remotion";
import { ClaudeCodeVideo } from "./ClaudeCodeVideo";

// 動画全体の設定
// fps: 30フレーム/秒
// durationInFrames: 30fps × 28秒 = 840フレーム
// width/height: 1920x1080 (フルHD)
export const Root = () => {
  return (
    <Composition
      id="ClaudeCodeVideo"
      component={ClaudeCodeVideo}
      durationInFrames={840}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
