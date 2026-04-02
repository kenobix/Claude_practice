import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

const CONNECTORS = [
  { name: "Google Drive",    emoji: "📁", role: "ドキュメント参照・要約",    color: "bg-yellow-500" },
  { name: "Gmail",           emoji: "✉️",  role: "メール確認・返信",          color: "bg-red-500"    },
  { name: "Google Calendar", emoji: "📅", role: "予定確認・作成",            color: "bg-blue-500"   },
  { name: "GitHub",          emoji: "🐙", role: "コードの読み書き・レビュー", color: "bg-gray-600"   },
  { name: "Notion",          emoji: "📝", role: "ナレッジ・タスク管理",      color: "bg-gray-700"   },
  { name: "TickTick",        emoji: "✅", role: "ToDo管理",                  color: "bg-blue-600"   },
  { name: "Figma",           emoji: "🎨", role: "デザイン参照・仕様確認",    color: "bg-purple-600" },
];

export const ConnectorsScene = () => {
  const frame = useCurrentFrame();

  const titleOpacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill className="flex flex-col items-center justify-center bg-gray-950 px-24">
      <h2
        style={{ opacity: titleOpacity }}
        className="text-5xl font-bold text-white mb-12"
      >
        連携できるコネクタ
      </h2>

      <div className="grid grid-cols-4 gap-6 w-full max-w-6xl">
        {CONNECTORS.map((connector, index) => {
          const delay = index * 12;
          const itemOpacity = interpolate(frame, [delay, delay + 20], [0, 1], { extrapolateRight: "clamp" });
          const itemY = interpolate(frame, [delay, delay + 20], [20, 0], { extrapolateRight: "clamp" });

          return (
            <div
              key={connector.name}
              style={{ opacity: itemOpacity, transform: `translateY(${itemY}px)` }}
              className="bg-gray-800 rounded-2xl p-6 flex flex-col items-start gap-3"
            >
              <div className={`w-12 h-12 ${connector.color} rounded-xl flex items-center justify-center text-2xl`}>
                {connector.emoji}
              </div>
              <div>
                <p className="text-white font-semibold text-lg">{connector.name}</p>
                <p className="text-gray-400 text-sm mt-1">{connector.role}</p>
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
