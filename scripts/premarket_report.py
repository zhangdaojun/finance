#!/usr/bin/env python3
"""Generate an A-share market brief and append it to a daily Google Doc."""

from __future__ import annotations

import base64
import json
import os
import sys
import textwrap
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
LOCAL_ENV = ROOT / ".env.local"
TZ = ZoneInfo("Asia/Shanghai")
UA = "Mozilla/5.0 (compatible; finance-market-brief/1.0)"


@dataclass
class MarketItem:
    name: str
    value: str
    change: str


def load_local_env() -> None:
    if not LOCAL_ENV.exists():
        return
    for raw_line in LOCAL_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def is_allowed_run_time(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 20 <= minutes <= 11 * 60 + 30) or (13 * 60 <= minutes <= 15 * 60)


def fetch_json(url: str, timeout: int = 15) -> dict[str, Any]:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "-", ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def eastmoney_clist(fs: str, fields: str, fid: str, pz: int = 80, po: int = 1) -> list[dict[str, Any]]:
    params = {"pn": 1, "pz": pz, "po": po, "np": 1, "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": 2, "invt": 2, "fid": fid, "fs": fs, "fields": fields}
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)
    data = fetch_json(url, timeout=12)
    return data.get("data", {}).get("diff") or []


def fetch_hot_boards() -> dict[str, list[dict[str, Any]]]:
    fields = "f12,f14,f2,f3,f4,f8,f20,f62"
    result: dict[str, list[dict[str, Any]]] = {"concepts": [], "industries": []}
    try:
        result["concepts"] = eastmoney_clist("m:90+t:3+f:!50", fields, "f3", pz=12)
    except Exception as exc:
        result["concept_error"] = [{"error": exc.__class__.__name__}]
    try:
        result["industries"] = eastmoney_clist("m:90+t:2+f:!50", fields, "f3", pz=12)
    except Exception as exc:
        result["industry_error"] = [{"error": exc.__class__.__name__}]
    return result


def fetch_eastmoney_news() -> list[dict[str, Any]]:
    url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=351&order=1&needInteractData=0&page_index=1&page_size=12&req_trace=1"
    try:
        data = fetch_json(url, timeout=12)
        return data.get("data", {}).get("list") or []
    except Exception:
        return []


def fetch_limit_up_codes() -> set[str]:
    today = datetime.now(TZ).strftime("%Y%m%d")
    url = "https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&d=" + today
    try:
        data = fetch_json(url, timeout=8)
        pool = data.get("data", {}).get("pool") or []
        return {str(item.get("c")) for item in pool if item.get("c")}
    except Exception:
        return set()


def limit_pct(code: str, name: str) -> float:
    if "ST" in name.upper():
        return 5.0
    if code.startswith(("300", "301", "688")):
        return 20.0
    if code.startswith(("920", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839")):
        return 30.0
    return 10.0


def normalize_board(row: dict[str, Any]) -> dict[str, Any]:
    return {"code": row.get("f12"), "name": row.get("f14"), "price": row.get("f2"), "pct": row.get("f3"), "turnover": row.get("f8"), "amount": row.get("f20"), "main_net_inflow": row.get("f62")}


def normalize_stock(row: dict[str, Any], zt_codes: set[str]) -> dict[str, Any]:
    code = str(row.get("f12") or "")
    name = str(row.get("f14") or "")
    pct = as_float(row.get("f3"))
    near_limit = code in zt_codes or pct >= limit_pct(code, name) - 0.25
    return {"code": code, "name": name, "price": row.get("f2"), "pct": row.get("f3"), "amount": row.get("f6"), "turnover": row.get("f8"), "main_net_inflow": row.get("f62"), "main_net_inflow_pct": row.get("f184"), "score": row.get("score"), "near_or_at_limit_up": near_limit}


def fetch_hot_stocks(zt_codes: set[str]) -> list[dict[str, Any]]:
    fields = "f12,f14,f2,f3,f5,f6,f8,f10,f20,f62,f184"
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    rows: dict[str, dict[str, Any]] = {}
    for fid, pz in (("f62", 180), ("f3", 180), ("f6", 140)):
        try:
            for row in eastmoney_clist(fs, fields, fid, pz=pz):
                code = str(row.get("f12") or "")
                name = str(row.get("f14") or "")
                pct = as_float(row.get("f3"))
                amount = as_float(row.get("f6"))
                net_inflow = as_float(row.get("f62"))
                if not code or not name or "ST" in name.upper():
                    continue
                if amount < 120_000_000 or net_inflow <= 0 or pct < 0.5:
                    continue
                row["score"] = round(pct * 1.8 + min(amount / 100_000_000, 120) * 0.08 + max(net_inflow / 100_000_000, 0) * 1.6 + max(as_float(row.get("f184")), 0) * 0.5, 2)
                rows[code] = row
        except Exception:
            continue
    sorted_rows = sorted(rows.values(), key=lambda item: item.get("score", 0), reverse=True)[:25]
    return [normalize_stock(row, zt_codes) for row in sorted_rows]


def fetch_global_markets() -> list[MarketItem]:
    import yfinance as yf

    tickers = {"^DJI": "Dow Jones", "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^HSI": "Hang Seng", "000001.SS": "Shanghai Composite", "399001.SZ": "Shenzhen Component", "CNH=X": "USD/CNH", "DX-Y.NYB": "Dollar Index", "^TNX": "US 10Y Yield", "CL=F": "WTI Crude", "GC=F": "Gold"}
    output: list[MarketItem] = []
    end = datetime.now(TZ)
    start = end - timedelta(days=7)
    for ticker, name in tickers.items():
        try:
            hist = yf.Ticker(ticker).history(start=start.date(), end=(end + timedelta(days=1)).date())
            if hist.empty:
                continue
            last = hist.iloc[-1]
            prev_close = hist.iloc[-2]["Close"] if len(hist) > 1 else last["Open"]
            close = float(last["Close"])
            pct = (close - float(prev_close)) / float(prev_close) * 100 if prev_close else 0
            output.append(MarketItem(name=name, value=f"{close:.2f}", change=f"{pct:+.2f}%"))
        except Exception:
            continue
    return output


def compact_source_data() -> dict[str, Any]:
    now = datetime.now(TZ)
    zt_codes = fetch_limit_up_codes()
    boards = fetch_hot_boards()
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "report_date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
        "global_markets": [item.__dict__ for item in fetch_global_markets()],
        "eastmoney_news": [{"title": item.get("title"), "source": item.get("mediaName"), "time": item.get("showTime"), "url": item.get("url") or item.get("shareUrl") or item.get("infoCode")} for item in fetch_eastmoney_news()],
        "hot_concepts": [normalize_board(row) for row in boards.get("concepts", [])[:10]],
        "hot_industries": [normalize_board(row) for row in boards.get("industries", [])[:10]],
        "hot_stocks": fetch_hot_stocks(zt_codes),
        "limit_up_codes_sample": sorted(zt_codes)[:80],
        "data_notes": ["GitHub Actions is scheduled for weekdays during A-share trading windows in Asia/Shanghai time.", "China holiday and exchange-closure handling is best-effort; cite exchange notices if news indicates a closure.", "Market data comes from public endpoints and may be delayed or incomplete."],
    }


def call_openai_report(source_data: dict[str, Any]) -> str:
    api_key = require_env("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1").strip() or "gpt-4.1"
    prompt = f"""
你是中文证券市场研究员。请基于下面 JSON 数据，写一份适合保存到 Google Docs 的 A 股交易时段市场与AI选股简报。

硬性要求：
- 使用中文。
- 必须明确“不构成投资建议”。
- 不要承诺收益，不要使用确定性买入/上涨措辞。
- 若当天可能是中国大陆 A 股休市日，生成简短休市说明、外围市场总结和后续关注点。
- 结构必须包含：标题、日期时间、摘要、全球经济与市场动态、A股盘面热点、行业主题与资金线索、候选关注股票、排除清单、关键风险、信息来源。
- “候选关注股票”选 3-6 只 A 股，说明入选逻辑、催化因素、关键风险、估值或技术面约束，以及哪些信息仍需进一步核实。
- 当前处于涨停或接近涨停状态的股票应放入排除清单，不纳入候选关注股票。
- 信息来源要列出来源名、时间和链接；如果数据源没有可用链接，说明为公开行情接口/公开新闻源。
- 对缺失或延迟的数据要直接说明，不要编造。

数据：
{json.dumps(source_data, ensure_ascii=False, indent=2)}
"""
    payload = {"model": model, "input": prompt, "temperature": 0.3, "max_output_tokens": 5000}
    resp = requests.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    text = data.get("output_text")
    if text:
        return text.strip() + "\n"
    parts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    if not parts:
        raise RuntimeError("OpenAI response did not include report text")
    return "\n".join(parts).strip() + "\n"


def google_credentials():
    from google.oauth2 import service_account

    raw = require_env("GOOGLE_SERVICE_ACCOUNT_JSON").strip()
    info = json.loads(raw) if raw.startswith("{") else json.loads(base64.b64decode(raw).decode("utf-8"))
    scopes = ["https://www.googleapis.com/auth/documents", "https://www.googleapis.com/auth/drive.file"]
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)


def drive_query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_daily_doc(drive, title: str) -> dict[str, str] | None:
    query_parts = ["mimeType='application/vnd.google-apps.document'", "trashed=false", f"name='{drive_query_literal(title)}'"]
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if folder_id:
        query_parts.append(f"'{drive_query_literal(folder_id)}' in parents")
    result = drive.files().list(q=" and ".join(query_parts), fields="files(id,name,webViewLink,modifiedTime)", orderBy="modifiedTime desc", pageSize=1).execute()
    files = result.get("files") or []
    return files[0] if files else None


def daily_doc_header(now: datetime) -> str:
    return f"\n\n---\n\n# 简报｜{now.strftime('%H:%M')} 北京时间\n\n"


def append_to_daily_google_doc(title: str, body: str, now: datetime) -> str:
    from googleapiclient.discovery import build

    creds = google_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    existing = find_daily_doc(drive, title)
    if existing:
        document_id = existing["id"]
        doc = docs.documents().get(documentId=document_id).execute()
        end_index = doc["body"]["content"][-1]["endIndex"] - 1
        docs.documents().batchUpdate(documentId=document_id, body={"requests": [{"insertText": {"location": {"index": end_index}, "text": daily_doc_header(now) + body}}]}).execute()
        return existing.get("webViewLink") or f"https://docs.google.com/document/d/{document_id}/edit"

    file_body: dict[str, Any] = {"name": title, "mimeType": "application/vnd.google-apps.document"}
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if folder_id:
        file_body["parents"] = [folder_id]
    created = drive.files().create(body=file_body, fields="id,webViewLink").execute()
    document_id = created["id"]
    first_text = f"# {title}\n" + daily_doc_header(now).lstrip() + body
    docs.documents().batchUpdate(documentId=document_id, body={"requests": [{"insertText": {"location": {"index": 1}, "text": first_text}}]}).execute()
    return created.get("webViewLink") or f"https://docs.google.com/document/d/{document_id}/edit"


def save_local_copy(title: str, report: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{title}.md"
    path.write_text(report, encoding="utf-8")
    return path


def main() -> int:
    load_local_env()
    now = datetime.now(TZ)
    if not is_allowed_run_time(now):
        print(f"Skip: {now.isoformat(timespec='seconds')} is outside the configured A-share weekday windows.")
        return 0

    title = f"中文市场与AI选股简报｜{now.strftime('%Y-%m-%d')}"
    try:
        source_data = compact_source_data()
        report = call_openai_report(source_data)
        local_path = save_local_copy(f"{title}_{now.strftime('%H%M')}", report)
        doc_url = append_to_daily_google_doc(title, report, now)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(textwrap.dedent(f"""
        Saved report: {title}
        Local copy: {local_path}
        Google Doc: {doc_url}
        """).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
