"""천안시 흡연장 찾기: 내 GPS 좌표에서 가까운 흡연장을 거리순으로 보여준다.

사용 예:
    python3 find_smoking.py data.csv --lat 36.8151 --lon 127.1139
    python3 find_smoking.py data.csv --lat 36.8151 --lon 127.1139 --top 10 --radius 1000

Python 표준 라이브러리만 사용한다.
"""
import argparse
import csv
import math
import sys

# 데이터마다 열 이름이 달라서, 아래 후보 중 처음 일치하는 열을 사용한다.
LAT_KEYS = ["위도", "lat", "latitude", "y", "Y좌표"]
LON_KEYS = ["경도", "lon", "lng", "longitude", "x", "X좌표"]
NAME_KEYS = ["이름", "시설명", "흡연구역명", "흡연시설명", "장소명", "name"]
ADDR_KEYS = ["주소", "소재지도로명주소", "도로명주소", "소재지지번주소", "소재지주소", "address"]

# 천안시 대략 범위. 이 범위를 벗어난 좌표는 경고만 하고 계산에는 포함한다.
CHEONAN_LAT = (36.6, 37.0)
CHEONAN_LON = (126.9, 127.4)


def haversine_m(lat1, lon1, lat2, lon2):
    """두 좌표 사이 직선거리(미터)."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def read_rows(path):
    """UTF-8(BOM 포함)을 먼저 시도하고, 실패하면 CP949로 읽는다."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                return list(reader), reader.fieldnames or [], enc
        except UnicodeDecodeError:
            continue
    sys.exit(f"오류: {path} 파일의 인코딩을 읽을 수 없습니다 (UTF-8, CP949 모두 실패).")


def pick(fieldnames, candidates):
    lower = {f.strip().lower(): f for f in fieldnames if f}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def main():
    ap = argparse.ArgumentParser(description="가까운 흡연장 찾기")
    ap.add_argument("csv_path", help="흡연장 목록 CSV")
    ap.add_argument("--lat", type=float, required=True, help="내 위치 위도")
    ap.add_argument("--lon", type=float, required=True, help="내 위치 경도")
    ap.add_argument("--top", type=int, default=5, help="보여줄 개수 (기본 5)")
    ap.add_argument("--radius", type=float, help="이 거리(m) 안의 흡연장만 표시")
    ap.add_argument("--lat-col", help="위도 열 이름 직접 지정")
    ap.add_argument("--lon-col", help="경도 열 이름 직접 지정")
    args = ap.parse_args()

    rows, fields, enc = read_rows(args.csv_path)
    lat_col = args.lat_col or pick(fields, LAT_KEYS)
    lon_col = args.lon_col or pick(fields, LON_KEYS)
    name_col = pick(fields, NAME_KEYS)
    addr_col = pick(fields, ADDR_KEYS)

    if not lat_col or not lon_col:
        sys.exit(
            "오류: 위도/경도 열을 찾지 못했습니다.\n"
            f"  파일의 열: {fields}\n"
            "  --lat-col, --lon-col 로 직접 지정하세요."
        )

    results, skipped, outside = [], 0, 0
    for r in rows:
        try:
            lat = float(r[lat_col])
            lon = float(r[lon_col])
        except (TypeError, ValueError):
            skipped += 1  # 좌표가 비었거나 숫자가 아닌 행
            continue
        if not (CHEONAN_LAT[0] <= lat <= CHEONAN_LAT[1] and CHEONAN_LON[0] <= lon <= CHEONAN_LON[1]):
            outside += 1
        d = haversine_m(args.lat, args.lon, lat, lon)
        if args.radius is None or d <= args.radius:
            results.append((d, r))

    results.sort(key=lambda x: x[0])

    print(f"파일: {args.csv_path} ({enc}), 전체 {len(rows)}행")
    print(f"사용한 열: 위도={lat_col}, 경도={lon_col}, 이름={name_col}, 주소={addr_col}")
    if skipped:
        print(f"좌표가 없거나 잘못되어 제외한 행: {skipped}")
    if outside:
        print(f"주의: 천안시 대략 범위를 벗어난 좌표 {outside}건 (위도·경도가 뒤바뀌었는지 확인)")
    print()

    if not results:
        print("조건에 맞는 흡연장이 없습니다.")
        return

    for i, (d, r) in enumerate(results[: args.top], 1):
        name = r.get(name_col, "") if name_col else ""
        addr = r.get(addr_col, "") if addr_col else ""
        dist = f"{d:,.0f} m" if d < 1000 else f"{d / 1000:.2f} km"
        print(f"{i:>2}. {dist:>9}  {name}  {addr}")
    print("\n※ 직선거리입니다. 실제 걷는 거리와 다를 수 있습니다.")


if __name__ == "__main__":
    main()
