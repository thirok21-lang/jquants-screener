
import io
import time
import zipfile
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

st.set_page_config(page_title="J-Quants 翌日候補", page_icon="📈", layout="wide")

st.title("📈 J-Quants 翌日候補スクリーナー")
st.caption("スマホ用Webアプリ｜寄り指値でエントリー → 当日引けで終了")

st.info(
    "毎日：①最新の jquants_history.zip を選択 → "
    "②J-Quants APIキー入力 → ③実行 → "
    "④候補確認 → ⑤更新済みZIPを保存"
)

uploaded = st.file_uploader("① jquants_history.zip を選択", type=["zip"])
api_key = st.text_input("② J-Quants APIキー", type="password")

run = st.button("③ 翌日候補を計算", type="primary", use_container_width=True)

def read_history(uploaded_file):
    raw = uploaded_file.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw), "r") as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError("ZIP内にCSVがありません。")
        target = "jquants_history.csv" if "jquants_history.csv" in names else names[0]
        with z.open(target) as f:
            df = pd.read_csv(f, dtype={"Code": str})
    df["Date"] = pd.to_datetime(df["Date"])
    return df

def update_history(df, api_key):
    need = ["Date","Code","AdjO","AdjH","AdjL","AdjC","AdjVo","Va"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"履歴データの必要列が不足しています: {missing}")

    url = "https://api.jquants.com/v2/equities/bars/daily"
    headers = {"x-api-key": api_key}

    last_date = df["Date"].max().date()
    today = datetime.now().date()
    d = last_date + timedelta(days=1)
    new_frames = []

    status = st.empty()

    while d <= today:
        if d.weekday() < 5:
            ds = d.strftime("%Y-%m-%d")
            status.write(f"不足データ取得中：{ds}")

            for retry in range(4):
                try:
                    r = requests.get(
                        url,
                        headers=headers,
                        params={"date": ds},
                        timeout=60
                    )

                    if r.status_code == 200:
                        rows = r.json().get("data", [])
                        if rows:
                            tmp = pd.DataFrame(rows)
                            for src, dst in {
                                "O":"AdjO","H":"AdjH","L":"AdjL",
                                "C":"AdjC","Vo":"AdjVo"
                            }.items():
                                if dst not in tmp.columns and src in tmp.columns:
                                    tmp[dst] = tmp[src]

                            if all(c in tmp.columns for c in need):
                                tmp = tmp[need].copy()
                                tmp["Date"] = pd.to_datetime(tmp["Date"])
                                tmp["Code"] = tmp["Code"].astype(str)
                                new_frames.append(tmp)
                        break

                    if r.status_code == 429:
                        status.warning(f"{ds}：API制限のため60秒待機")
                        time.sleep(60)
                        continue

                    status.error(f"{ds}：HTTP {r.status_code}")
                    break

                except Exception as e:
                    if retry == 3:
                        status.warning(f"{ds}：取得できませんでした")
                    else:
                        time.sleep(10)

            time.sleep(15)
        d += timedelta(days=1)

    status.empty()

    if new_frames:
        df = pd.concat([df] + new_frames, ignore_index=True)

    df = (
        df.drop_duplicates(subset=["Date","Code"])
          .sort_values(["Code","Date"])
          .reset_index(drop=True)
    )
    return df

def screen(df):
    g = df.groupby("Code", group_keys=False)

    df["MA5"]  = g["AdjC"].transform(lambda s: s.rolling(5).mean())
    df["MA25"] = g["AdjC"].transform(lambda s: s.rolling(25).mean())
    df["MA75"] = g["AdjC"].transform(lambda s: s.rolling(75).mean())
    df["MA25_5ago"] = g["MA25"].shift(5)

    df["Dev25"] = (df["AdjC"] / df["MA25"] - 1) * 100
    df["Ret5"] = g["AdjC"].pct_change(5) * 100
    df["PrevH"] = g["AdjH"].shift(1)
    df["PrevL"] = g["AdjL"].shift(1)
    df["Vol5"] = g["AdjVo"].transform(lambda s: s.rolling(5).mean())

    latest = df["Date"].max()
    x = df[df["Date"] == latest].copy()

    cond = (
        (x["MA5"] > x["MA25"]) &
        (x["MA25"] > x["MA75"]) &
        (x["MA25"] > x["MA25_5ago"]) &
        (x["AdjC"] > x["MA25"]) &
        (x["Dev25"].between(0, 3)) &
        (x["Ret5"] <= 0) &
        (x["AdjC"] < x["AdjO"]) &
        (x["AdjH"] > x["PrevH"]) &
        (x["AdjL"] > x["PrevL"]) &
        (x["AdjVo"] <= x["Vol5"]) &
        (x["AdjC"] >= 500) &
        (x["Va"] >= 2_000_000_000)
    )

    cand = x.loc[cond, [
        "Code","AdjC","Va","MA5","MA25","MA75",
        "Dev25","Ret5","AdjVo","Vol5"
    ]].copy()

    cand["寄り指値目安(+0.5%)"] = cand["AdjC"] * 1.005
    cand["売買代金(億円)"] = cand["Va"] / 1e8
    cand["出来高5日比"] = cand["AdjVo"] / cand["Vol5"]

    cand = cand[[
        "Code","AdjC","寄り指値目安(+0.5%)",
        "Dev25","Ret5","出来高5日比","売買代金(億円)",
        "MA5","MA25","MA75"
    ]].sort_values(
        ["Dev25","売買代金(億円)"],
        ascending=[True, False]
    ).reset_index(drop=True)

    return latest, cand

def make_outputs(df, cand):
    need = ["Date","Code","AdjO","AdjH","AdjL","AdjC","AdjVo","Va"]

    csv_buf = io.BytesIO()
    df[need].to_csv(csv_buf, index=False, encoding="utf-8-sig")
    csv_bytes = csv_buf.getvalue()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("jquants_history.csv", csv_bytes)

    cand_buf = io.BytesIO()
    cand.to_csv(cand_buf, index=False, encoding="utf-8-sig")

    return zip_buf.getvalue(), cand_buf.getvalue()

if run:
    if uploaded is None:
        st.error("jquants_history.zip を選択してください。")
        st.stop()

    if not api_key.strip():
        st.error("J-Quants APIキーを入力してください。")
        st.stop()

    try:
        with st.spinner("履歴データを読み込んでいます…"):
            df = read_history(uploaded)

        st.write(
            f"履歴最終日：**{df['Date'].max().date()}** "
            f"／ データ件数：**{len(df):,}件**"
        )

        with st.spinner("最新データを確認しています…"):
            df = update_history(df, api_key.strip())

        with st.spinner("候補銘柄を計算しています…"):
            latest, cand = screen(df)

        st.success(f"基準日：{latest.date()} ／ 候補：{len(cand)}銘柄")

        if len(cand) == 0:
            st.warning("本日の候補はありません。無理に取引しません。")
        else:
            show = cand.copy()
            for c in ["AdjC","寄り指値目安(+0.5%)","MA5","MA25","MA75"]:
                show[c] = show[c].round(2)
            for c in ["Dev25","Ret5","出来高5日比","売買代金(億円)"]:
                show[c] = show[c].round(2)

            st.dataframe(show, use_container_width=True, hide_index=True)

        history_zip, candidates_csv = make_outputs(df, cand)

        st.download_button(
            "⑤ 次回用 jquants_history.zip を保存",
            history_zip,
            file_name="jquants_history.zip",
            mime="application/zip",
            use_container_width=True
        )

        st.download_button(
            "候補一覧CSVを保存",
            candidates_csv,
            file_name="latest_candidates.csv",
            mime="text/csv",
            use_container_width=True
        )

        st.caption(
            "取引ルール：寄り指値でエントリーし、利確・損切注文は置かず、当日午後の引けで終了。"
        )

    except Exception as e:
        st.error(f"処理中にエラーが発生しました：{e}")
