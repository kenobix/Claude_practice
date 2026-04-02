import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

const POINTS = [
  "複数アプリの操作が Claude ひとつで完結",
  "チャット入力だけで外部情報の取得・操作が可能",
  "カスタムコネクタ（MCP）で対応サービスを拡張できる",
  "コード・デザイン・タスクを横断した指示が出せる",
];

export const SummaryScene = () => {
  const frame = useCurrentFrame();

  const titleOpacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill className="flex flex-col items-center justify-center bg-gray-950 px-32">
      <h2
        style={{ opacity: titleOpacity }}
        className="text-6xl font-bold text-white mb-14"
      >
        まとめ
      </h2>

      <div className="flex flex-col gap-6 w-full max-w-4xl">
        {POINTS.map((point, index) => {
          const delay = index * 20 + 10;
          const opacity = interpolate(frame, [delay, delay + 20], [0, 1], { extrapolateRight: "clamp" });
          const x = interpolate(frame, [delay, delay + 20], [-30, 0], { extrapolateRight: "clamp" });

          return (
            <div
              key={index}
              style={{ opacity, transform: `translateX(${x}px)` }}
              className="flex items-center gap-5"
            >
              <div className="w-10 h-10 rounded-full bg-orange-500 flex items-center justify-center text-white font-bold text-xl flex-shrink-0">
                {index + 1}
              </div>
              <p className="text-2xl text-gray-200">{point}</p>
            </div>
          );
        })}
      </div>

      {/* フッター */}
      {(() => {
        const footerOpacity = interpolate(frame, [100, 130], [0, 1], { extrapolateRight: "clamp" });
        return (
          <p style={{ opacity: footerOpacity }} className="mt-16 text-gray-500 text-xl">
            Powered by Anthropic · Claude Code
          </p>
        );
      })()}
    </AbsoluteFill>
  );
};
