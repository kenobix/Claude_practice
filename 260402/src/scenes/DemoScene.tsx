import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

const DEMO_ITEMS = [
  { input: "今日の予定を教えて",              output: "Google Calendarから3件の予定を取得しました" },
  { input: "昨日のメールを要約して",          output: "Gmailから5件のメールを要約しました" },
  { input: "このタスクをTickTickに追加して", output: "ToDoを追加しました: ✅ 完了" },
  { input: "GitHubのPRをレビューして",       output: "3件の改善点を検出し、コメントを作成しました" },
];

export const DemoScene = () => {
  const frame = useCurrentFrame();

  const titleOpacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill className="flex flex-col items-center justify-center bg-gray-950 px-32">
      <h2
        style={{ opacity: titleOpacity }}
        className="text-5xl font-bold text-white mb-12"
      >
        チャットで外部サービスを操作
      </h2>

      <div className="flex flex-col gap-6 w-full max-w-5xl">
        {DEMO_ITEMS.map((item, index) => {
          const delay = index * 25;
          const itemOpacity = interpolate(frame, [delay, delay + 20], [0, 1], { extrapolateRight: "clamp" });
          const outputDelay = delay + 15;
          const outputOpacity = interpolate(frame, [outputDelay, outputDelay + 15], [0, 1], { extrapolateRight: "clamp" });

          return (
            <div key={index} style={{ opacity: itemOpacity }} className="flex flex-col gap-2">
              {/* ユーザー入力 */}
              <div className="flex justify-end">
                <div className="bg-orange-500 text-white rounded-2xl rounded-tr-sm px-6 py-3 text-xl max-w-xl">
                  {item.input}
                </div>
              </div>
              {/* Claude応答 */}
              <div style={{ opacity: outputOpacity }} className="flex justify-start">
                <div className="bg-gray-700 text-gray-100 rounded-2xl rounded-tl-sm px-6 py-3 text-xl max-w-2xl">
                  {item.output}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
