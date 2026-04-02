import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

export const TitleScene = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleOpacity = spring({ frame, fps, config: { damping: 12 } });
  const subtitleOpacity = interpolate(frame, [15, 45], [0, 1], { extrapolateRight: "clamp" });
  const titleY = interpolate(spring({ frame, fps, config: { damping: 12 } }), [0, 1], [40, 0]);

  return (
    <AbsoluteFill className="flex flex-col items-center justify-center bg-gray-950">
      {/* ロゴアイコン風の装飾 */}
      <div
        style={{ opacity: titleOpacity, transform: `translateY(${titleY}px)` }}
        className="w-24 h-24 rounded-2xl bg-orange-500 flex items-center justify-center mb-8"
      >
        <span className="text-white text-5xl font-bold">C</span>
      </div>

      {/* メインタイトル */}
      <h1
        style={{ opacity: titleOpacity, transform: `translateY(${titleY}px)` }}
        className="text-8xl font-bold text-white tracking-tight"
      >
        Claude Code
      </h1>

      {/* サブタイトル */}
      <p
        style={{ opacity: subtitleOpacity }}
        className="mt-6 text-3xl text-gray-400 tracking-wide"
      >
        AIエージェントで、作業をチャットひとつで完結する
      </p>
    </AbsoluteFill>
  );
};
