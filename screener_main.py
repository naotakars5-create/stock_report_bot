"""
screener_main.py

スクリーナーのモード切替ディスパッチャ（両方残し・1コマンドで切替）。

  --mode value   … 業種別PERランキング（割安版・sector_per_main）※J-Quants必要
  --mode pattern … 上昇チャートパターン（pattern_main）※株価のみ・J-Quants不要

環境変数 SCREEN_MODE でも指定可（workflow から渡す）。既定は pattern。

配信層・データ取得層は各モードが既存のものをそのまま流用する（新系統を作らない）。
"""

import argparse
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(description="スクリーナー（モード切替）")
    ap.add_argument("--mode", choices=["value", "pattern"],
                    default=(os.environ.get("SCREEN_MODE") or "pattern").strip().lower())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.mode == "value":
        import sector_per_main
        print("[モード] value（業種別PER割安ランキング）")
        return sector_per_main.main(dry_run=args.dry_run)
    else:
        import pattern_main
        print("[モード] pattern（上昇チャートパターン）")
        return pattern_main.main(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
