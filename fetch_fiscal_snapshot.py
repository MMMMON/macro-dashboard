import csv
import io
import json
import os
import time
import urllib.parse
import urllib.request

OUTPUT = os.path.join(os.path.dirname(__file__), "fiscal_snapshot.json")
UA = "Bessent Fiscal Dashboard Data Pipeline/1.0"


def request(url, *, data=None, headers=None, attempts=3, timeout=45):
    merged = {"User-Agent": UA, "Accept": "application/json,text/csv,*/*"}
    merged.update(headers or {})
    error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=merged)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise error


def fred(series_id, start="2000-01-01"):
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode({"id": series_id, "cosd": start})
    rows = csv.DictReader(io.StringIO(request(url).decode("utf-8-sig")))
    result = []
    for row in rows:
        raw = row.get(series_id)
        if raw in (None, "", "."):
            continue
        result.append({"date": row.get("DATE") or row.get("observation_date"), "value": float(raw)})
    if not result:
        raise RuntimeError(f"FRED {series_id} returned no observations")
    return result


def fiscal(path):
    url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/" + path
    return json.loads(request(url).decode("utf-8"))


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_payload():
    errors = []
    series = {}
    for series_id in ["GDP", "GDPC1", "GDPDEF", "DFF", "THREEFYTP10"]:
        try:
            series[series_id] = fred(series_id, "2024-01-01" if series_id == "DFF" else "2000-01-01")
        except Exception as exc:
            errors.append(f"FRED {series_id}: {exc}")

    debt = None
    average_interest = None
    auctions = []
    try:
        item = fiscal("v2/accounting/od/debt_to_penny?sort=-record_date&page%5Bsize%5D=1")["data"][0]
        debt = {"date": item["record_date"], "heldByPublic": number(item["debt_held_public_amt"]), "intragovernmental": number(item["intragov_hold_amt"]), "total": number(item["tot_pub_debt_out_amt"])}
    except Exception as exc:
        errors.append(f"Debt: {exc}")
    try:
        rows = fiscal("v2/accounting/od/avg_interest_rates?sort=-record_date&page%5Bsize%5D=100")["data"]
        item = next((row for row in rows if "Total Marketable" in row.get("security_desc", "")), None) or next((row for row in rows if "Marketable" in row.get("security_desc", "")), None)
        if item:
            average_interest = {"date": item["record_date"], "value": number(item["avg_interest_rate_amt"]), "security": item["security_desc"]}
    except Exception as exc:
        errors.append(f"Average interest: {exc}")
    try:
        rows = fiscal("v1/accounting/od/auctions_query?sort=-auction_date&page%5Bsize%5D=100")["data"]
        for item in [row for row in rows if str(row.get("security_term", "")).startswith(("10-Year", "20-Year", "30-Year"))][:12]:
            accepted = number(item.get("total_accepted")) or number(item.get("offering_amt")) or 0
            indirect, direct, dealer = number(item.get("indirect_bidder_accepted")), number(item.get("direct_bidder_accepted")), number(item.get("primary_dealer_accepted"))
            indirect_share = indirect / accepted * 100 if accepted and indirect is not None else None
            direct_share = direct / accepted * 100 if accepted and direct is not None else None
            dealer_share = dealer / accepted * 100 if accepted and dealer is not None else None
            btc = number(item.get("bid_to_cover_ratio"))
            stress = None if btc is None else max(0, min(100, 50 + (2.5 - btc) * 45 + (0 if dealer_share is None else (dealer_share - 12) * 1.2) + (0 if indirect_share is None else (65 - indirect_share) * .45)))
            auctions.append({"term": item.get("security_term"), "auctionDate": item.get("auction_date"), "offering": number(item.get("offering_amt")), "highYield": number(item.get("high_yield")), "bidToCover": btc, "indirectShare": indirect_share, "directShare": direct_share, "dealerShare": dealer_share, "stress": stress})
    except Exception as exc:
        errors.append(f"Auctions: {exc}")

    status = {name: {"state": "success"} for name in ["fred", "debt", "averageInterest", "auctions"]}
    if any(message.startswith("FRED ") for message in errors): status["fred"] = {"state": "stale"}
    if any(message.startswith("Debt:") for message in errors): status["debt"] = {"state": "stale"}
    if any(message.startswith("Average interest:") for message in errors): status["averageInterest"] = {"state": "stale"}
    if any(message.startswith("Auctions:") for message in errors): status["auctions"] = {"state": "stale"}
    return {"series": series, "debt": debt, "averageInterest": average_interest, "auctions": auctions, "sourceStatus": status, "errors": errors}


def main():
    payload = build_payload()
    payload["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()

