import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

export const OverviewScene = () => {
  const frame = useCurrentFrame();

  const opacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const y = interpolate(frame, [0, 20], [30, 0], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill
      style={{ opacity, transform: `translateY(${y}px)` }}
      className="flex flex-col items-center justify-center bg-gray-950 px-32"
    >
      <h2 className="text-6xl font-bold text-white mb-12">Claude Code とは？</h2>

      <p className="text-3xl text-gray-300 text-center leading-relaxed max-w-5xl">
        Claude Code は Anthropic が提供する
        <span className="text-orange-400 font-semibold"> AIエージェント </span>
        です。<br />
        チャットに自然言語で指示するだけで、<br />
        外部サービスの操作・コードの読み書き・情報収集など<br />
        <span className="text-white font-semibold">これまで手動でやっていた作業を自動で実行</span>
        できます。
      </p>
    </AbsoluteFill>
  );
};
