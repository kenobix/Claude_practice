"""
Google Places API（Nearby Search）の薄いラッパー。

同じ座標付近への問い合わせを使い回すため、座標を丸めてキャッシュする
（ペルソナが近い場所を何度も訪れる/複数ペルソナが近くにいる場合のAPI呼び出し削減）。

必要な環境変数:
  GOOGLE_MAPS_API_KEY  Google Cloud ConsoleでPlaces API (Legacy)を有効化して取得したキー
"""

import os
import math
import requests

NEARBY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
CACHE_PRECISION = 4  # 小数点以下4桁(約11m)単位でキャッシュキーをまとめる


def _haversine_m(lat1, lng1, lat2, lng2):
    """2点間の距離をメートルで返す。"""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class PlacesClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "GOOGLE_MAPS_API_KEY が設定されていません。\n"
                "  export GOOGLE_MAPS_API_KEY='your-api-key'"
            )
        self._cache: dict[tuple, list[dict]] = {}

    def nearby(self, lat: float, lng: float, radius_m: int = 500) -> list[dict]:
        """指定座標の周辺施設一覧を返す（name, place_id, lat, lng, types, rating, distance_m）。"""
        key = (round(lat, CACHE_PRECISION), round(lng, CACHE_PRECISION), radius_m)
        if key in self._cache:
            return self._cache[key]

        resp = requests.get(
            NEARBY_SEARCH_URL,
            params={
                "location": f"{lat},{lng}",
                "radius": radius_m,
                "key": self.api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            raise RuntimeError(f"Places API エラー: {status} - {data.get('error_message', '')}")

        results = []
        for r in data.get("results", []):
            p_lat = r["geometry"]["location"]["lat"]
            p_lng = r["geometry"]["location"]["lng"]
            results.append({
                "name": r.get("name"),
                "place_id": r.get("place_id"),
                "lat": p_lat,
                "lng": p_lng,
                "types": r.get("types", []),
                "rating": r.get("rating"),
                "distance_m": round(_haversine_m(lat, lng, p_lat, p_lng), 1),
            })

        results.sort(key=lambda x: x["distance_m"])
        self._cache[key] = results
        return results
